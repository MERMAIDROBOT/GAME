"""Local bounded scenario runs. Definitions and all captured output stay private."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import time

HEARTBEAT = re.compile(r'\[port-lab\] pc=(0x[0-9a-f]+) ra=(0x[0-9a-f]+) eeCycles=(\d+) dma=(\d+) gif=(\d+) gs=(\d+) threads=(\d+)', re.I)
FRAME = re.compile(r'\[port-lab frame\] (\d+)x(\d+) nonblack=(\d+)')


def read_ppm(data):
    parts = data.split(b'\n', 3)
    if len(parts) != 4 or parts[0] != b'P6' or parts[2] != b'255':
        return None
    try:
        w, h = map(int, parts[1].split())
    except ValueError:
        return None
    if not 1 <= w <= 4096 or not 1 <= h <= 4096 or len(parts[3]) != w*h*3:
        return None
    return w, h, parts[3]


class VisualSteps:
    """Match private RGB reference regions before issuing local pad commands."""
    def __init__(self, private, steps, command, post_actions=None):
        if not isinstance(steps, list) or len(steps) > 64:
            raise ValueError('Visual steps must be a list of at most 64 entries')
        self.steps, self.assets, self.events = [], {}, []
        self.command, self.index, self.last_time, self.last_image = command, 0, 0, None
        self.post_actions = [] if post_actions is None else post_actions
        self.post_index = 0
        if (not isinstance(self.post_actions, list) or len(steps)+len(self.post_actions)>64 or
            (self.post_actions and not steps)):
            raise ValueError('Post-navigation actions need visual navigation and at most 64 total actions')
        previous = -1.0
        for action in self.post_actions:
            offset = action.get('offset_seconds')
            mask, hold = action.get('button_mask',0), action.get('hold_ms',250)
            stick = action.get('left_stick',[128,128])
            if (type(offset) not in (int,float) or not 0 <= offset <= 600 or offset <= previous or
                type(mask) is not int or not 0 <= mask <= 65535 or
                type(hold) is not int or not 1 <= hold <= 10000 or
                not isinstance(stick,list) or len(stick)!=2 or
                any(type(v) is not int or not 0 <= v <= 255 for v in stick)):
                raise ValueError('Post-navigation timing or controller action is invalid')
            previous = offset
        for step in steps:
            ref = (private / step['reference']).resolve()
            if not ref.is_relative_to(private.resolve()):
                raise ValueError('Visual references must stay in private game storage')
            data = ref.read_bytes(); image = read_ppm(data)
            if image is None:
                raise ValueError('Visual reference must be a complete RGB PPM')
            region = step['region']
            if (not isinstance(region, list) or len(region) != 4 or
                any(type(v) is not int for v in region)):
                raise ValueError('Visual region needs four integer coordinates')
            x, y, w, h = region
            if min(x,y) < 0 or min(w,h) <= 0 or x+w > image[0] or y+h > image[1]:
                raise ValueError('Visual region is outside the reference image')
            mask, hold = step.get('button_mask',0), step.get('hold_ms',250)
            stick = step.get('left_stick',[128,128])
            threshold, delay, timeout = step.get('max_mean_error',8), step.get('min_delay_seconds',1), step.get('timeout_seconds',90)
            if (type(mask) is not int or not 0 <= mask <= 65535 or type(hold) is not int or not 1 <= hold <= 10000 or
                not isinstance(stick,list) or len(stick)!=2 or any(type(v) is not int or not 0 <= v <= 255 for v in stick) or
                type(threshold) not in (int,float) or not 0 <= threshold <= 50 or
                type(delay) not in (int,float) or not 0 <= delay <= 600 or
                type(timeout) not in (int,float) or not 1 <= timeout <= 600):
                raise ValueError('Visual input action or matching bounds are invalid')
            block=step.get('match_block_size',1)
            fine=step.get('max_fine_mean_error',30)
            if (type(block) is not int or not 1 <= block <= 8 or
                type(fine) not in (int,float) or not 0 <= fine <= 50):
                raise ValueError('Visual aggregation or fine error bound is invalid')
            alternatives=step.get('reference_alternatives',[])
            if (not isinstance(alternatives,list) or len(alternatives)>2 or
                any(not isinstance(name,str) or not name for name in alternatives)):
                raise ValueError('Visual alternatives must name at most two private references')
            variants=[]
            for name in [step['reference'],*alternatives]:
                candidate=(private/name).resolve()
                if not candidate.is_relative_to(private.resolve()):
                    raise ValueError('Visual references must stay in private game storage')
                candidate_data=candidate.read_bytes()
                candidate_image=read_ppm(candidate_data)
                if candidate_image is None or candidate_image[:2]!=image[:2]:
                    raise ValueError('Visual alternatives must be complete RGB PPMs with matching dimensions')
                relative=str(candidate.relative_to(private.resolve()))
                digest=hashlib.sha256(candidate_data).hexdigest()
                variants.append((self.region(candidate_image,region),relative,digest))
                self.assets[relative]=digest
            self.steps.append((step,image[:2],variants))

    @staticmethod
    def region(image, region):
        width, _, pixels=image; x,y,w,h=region
        return b''.join(pixels[((y+row)*width+x)*3:((y+row)*width+x+w)*3] for row in range(h))

    @staticmethod
    def match_errors(actual, expected, step):
        fine=sum(abs(a-b) for a,b in zip(actual,expected))/len(expected)
        block=step.get('match_block_size',1)
        if block==1:
            return fine,fine
        # Compare local area averages across small rasterization changes, while
        # retaining a fine-pixel cap to reject aliased or structurally wrong output.
        if fine>step.get('max_fine_mean_error',30):
            return float('inf'),fine
        _,_,w,h=step['region']
        error=0;count=0
        for y in range(0,h,block):
            for x in range(0,w,block):
                positions=[(yy*w+xx)*3 for yy in range(y,min(y+block,h))
                           for xx in range(x,min(x+block,w))]
                for channel in range(3):
                    error+=abs(sum(actual[i+channel] for i in positions)-
                               sum(expected[i+channel] for i in positions))/len(positions)
                    count+=1
        return error/count,fine

    @staticmethod
    def match_variants(actual, variants, step):
        # Every alternative keeps the same ROI, threshold and fine-pixel cap.
        matches=[]
        for pixels,path,digest in variants:
            error,fine=VisualSteps.match_errors(actual,pixels,step)
            matches.append((error,fine,path,digest))
        return min(matches,key=lambda match:(match[0],match[1]))

    def update(self, data, elapsed, acknowledged_sequences=None):
        # One file carries commands. Never overwrite an unconsumed command.
        if (acknowledged_sequences is not None and self.events and
            self.events[-1]['sequence'] not in acknowledged_sequences):
            return
        if self.index == len(self.steps):
            if self.post_index < len(self.post_actions):
                action = self.post_actions[self.post_index]
                if elapsed-self.last_time >= action['offset_seconds']:
                    self.publish(action, elapsed, source='post_navigation')
                    self.post_index += 1
            return
        step, dimensions, expected=self.steps[self.index]
        if elapsed-self.last_time > step.get('timeout_seconds',90):
            raise TimeoutError('Visual step timed out: '+str(step.get('name',self.index)))
        if elapsed-self.last_time < step.get('min_delay_seconds',1):
            return
        digest=hashlib.sha256(data).hexdigest()
        if digest == self.last_image:
            return
        image=read_ppm(data)
        if image is None or image[:2] != dimensions:
            return
        actual=self.region(image,step['region'])
        error,fine_error,reference,reference_digest=self.match_variants(actual,expected,step)
        if error > step.get('max_mean_error',8):
            return
        self.publish(step, elapsed, image_sha256=digest, mean_error=error,
                     fine_mean_error=fine_error,reference=reference,reference_sha256=reference_digest,match_block_size=step.get('match_block_size',1))
        self.index+=1;self.last_time=elapsed;self.last_image=digest

    @property
    def completed(self):
        return self.index==len(self.steps) and self.post_index==len(self.post_actions)

    def publish(self, step, elapsed, **evidence):
        mask,hold=step.get('button_mask',0),step.get('hold_ms',250)
        x,y=step.get('left_stick',[128,128]);sequence=len(self.events)+1
        temporary=self.command.with_suffix('.next')
        temporary.write_text(f'{sequence} {mask} {hold} {x} {y}\n')
        temporary.replace(self.command)
        self.events.append({'step':step.get('name',str(sequence)), 'sequence':sequence,
                            'seconds':round(elapsed,3), 'button_mask':mask,
                            'hold_ms':hold,'left_stick':[x,y], **evidence})


class VisualPostconditions:
    """Observe ordered results after acknowledged inputs; never issue pad commands.

    Passing proves the specified sampled regions matched, not complete gameplay.
    A capture token must change after the ACK; a stale latest.ppm cannot pass.
    """
    def __init__(self, private, conditions, action_count):
        if not isinstance(conditions,list) or len(conditions)>64:
            raise ValueError('Postconditions must be a list of at most 64 entries')
        previous=0
        for condition in conditions:
            sequence=condition.get('after_sequence')
            stable=condition.get('stable_seconds',1)
            timeout=condition.get('timeout_seconds',90)
            frames=condition.get('min_frames',2)
            gap=condition.get('max_sample_gap_seconds',3)
            if (type(sequence) is not int or not 1 <= sequence <= action_count or sequence<previous):
                raise ValueError('Postcondition must name an issued action in sequence order')
            if (type(timeout) not in (int,float) or not 1 <= timeout <= 600 or
                type(stable) not in (int,float) or not 0 <= stable <= timeout or
                type(frames) is not int or not 2 <= frames <= 600 or
                type(gap) not in (int,float) or not 0 < gap <= 600):
                raise ValueError('Postcondition sampling bounds are invalid')
            if any(k in condition for k in ('button_mask','hold_ms','left_stick')):
                raise ValueError('Postconditions observe results and cannot issue controller actions')
            previous=sequence
        # Reuse private-path, PPM, ROI and threshold validation without publishing.
        matcher=VisualSteps(private,conditions,private/'unused-postcondition-command')
        self.steps,self.assets=matcher.steps,matcher.assets
        self.index,self.events=0,[]
        self.ack_times={}
        self.started=None
        self.last_token=None
        self.match_start=None
        self.last_match=None
        self.frames=0
        self.error=None

    @property
    def completed(self):
        return bool(self.steps) and self.index==len(self.steps) and not self.error

    def blocks_action(self, next_sequence):
        return bool(self.steps and not self.completed and
                    next_sequence>self.steps[self.index][0]['after_sequence'])

    def update(self, data, elapsed, acknowledged_sequences, capture_token):
        for sequence in acknowledged_sequences:
            self.ack_times.setdefault(sequence,elapsed)
        if not self.steps or self.completed or self.error:
            return
        condition,dimensions,expected=self.steps[self.index]
        sequence=condition['after_sequence']
        if sequence not in self.ack_times:
            return
        if self.started is None:
            self.started=self.ack_times[sequence]
            self.last_token=capture_token
            return
        if elapsed-self.started > condition.get('timeout_seconds',90):
            self.error='Postcondition timed out: '+str(condition.get('name',self.index))
            raise TimeoutError(self.error)
        if elapsed-self.started < condition.get('min_delay_seconds',1):
            self.last_token=capture_token
            return
        if capture_token is None or capture_token==self.last_token:
            return
        self.last_token=capture_token
        image=read_ppm(data)
        if image is None:
            return # Partial writes are not evidence either way.
        actual=VisualSteps.region(image,condition['region']) if image[:2]==dimensions else None
        error,fine_error,reference,reference_digest=VisualSteps.match_variants(actual,expected,condition) if actual is not None else (None,None,None,None)
        if error is None or error>condition.get('max_mean_error',8):
            self.match_start=self.last_match=None
            self.frames=0
            return
        if self.last_match is not None and elapsed-self.last_match>condition.get('max_sample_gap_seconds',3):
            self.match_start=None
            self.frames=0
        if self.match_start is None:
            self.match_start=elapsed
        self.last_match=elapsed
        self.frames+=1
        if self.frames>=condition.get('min_frames',2) and elapsed-self.match_start>=condition.get('stable_seconds',1):
            self.events.append({'name':condition.get('name',str(self.index)),
                'after_sequence':sequence,'seconds':round(elapsed,3),
                'matched_seconds':round(elapsed-self.match_start,3),'frames':self.frames,
                'mean_error':error,'fine_mean_error':fine_error,
                'reference':reference,'reference_sha256':reference_digest,
                'match_block_size':condition.get('match_block_size',1),
                'image_sha256':hashlib.sha256(data).hexdigest()})
            self.index+=1
            self.started=self.match_start=self.last_match=None
            self.frames=0

    def result(self):
        return {'status':'failed' if self.error else 'passed' if self.completed else
                'incomplete' if self.steps else 'not_requested',
                'required':len(self.steps),'passed':len(self.events),'events':self.events,
                'error':self.error,'gameplay_verified':False,
                'scope':'Ordered sampled visual regions after acknowledged inputs; not full rendering or sustained gameplay verification.'}


def completed_flip_timing(text, after_sequence=None, warmup_seconds=5, window_seconds=15):
    """Guest flip final-return cadence; not monitor delivery or simulation speed."""
    if after_sequence is not None:
        match=re.search(r'\[port-lab pad-command\] seq='+str(after_sequence)+r'\b',text)
        if match is None:
            return {'samples':0,'verified_display_fps':False,'reason':'measurement input not acknowledged'}
        text=text[match.end():]
    times=[int(n) for n in re.findall(r'\[port-lab completed-flip\] ns=(\d+)',text)]
    window_complete = bool(times and times[-1]-times[0] >= (warmup_seconds+window_seconds)*1e9)
    if times:
        first=times[0];times=[n for n in times if warmup_seconds*1e9 <= n-first <= (warmup_seconds+window_seconds)*1e9]
    if len(times)<2:
        return {'samples':len(times),'verified_display_fps':False,'reason':'insufficient completed flip markers'}
    intervals=[(b-a)/1e6 for a,b in zip(times,times[1:])]
    if any(n<=0 for n in intervals):
        raise ValueError('Completed flip timestamps must increase strictly')
    ordered=sorted(intervals)
    def percentile(p):
        position=(len(ordered)-1)*p;lo=int(position);hi=min(lo+1,len(ordered)-1)
        return ordered[lo]+(ordered[hi]-ordered[lo])*(position-lo)
    return {'samples':len(times),'seconds':(times[-1]-times[0])/1e9,
            'completed_flips_per_second':1000/(sum(intervals)/len(intervals)),
            'frame_interval_ms_p50':percentile(.5),'frame_interval_ms_p95':percentile(.95),
            'frame_interval_ms_p99':percentile(.99),'warmup_seconds':warmup_seconds,
            'after_sequence':after_sequence,'requested_window_seconds':window_seconds,'window_complete':window_complete,'verified_display_fps':False,
            'scope':'Final returns from the privately instrumented game flip function; not monitor delivery, original PS2 performance, or simulation-speed validation.'}


def compare_scenarios(private, baseline, candidate, name):
    import pipeline as lab
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', n) for n in (baseline, candidate, name)):
        raise ValueError('Comparison names must be lowercase letters, digits, hyphens or underscores')
    root = private / 'logs/scenarios'
    before = json.loads((root / baseline / 'report.json').read_text())
    after = json.loads((root / candidate / 'report.json').read_text())
    if before.get('input_assets',{}) != after.get('input_assets',{}):
        raise ValueError('Cannot compare runs with different visual reference inputs')
    if before['definition_sha256'] != after['definition_sha256']:
        raise ValueError('Cannot compare runs with different scenario definitions')
    for run, record in ((baseline,before),(candidate,after)):
        sequence=record.get('completed_flip_timing',{}).get('after_sequence')
        if sequence is not None:
            record['completed_flip_timing']=completed_flip_timing((root/run/'runner.log').read_text(errors='replace'),sequence)
    output = root / name
    output.mkdir(parents=True, exist_ok=False)
    keys = ('status', 'build_sha256', 'outcome', 'heartbeat_samples', 'frame_samples',
            'almost_black_fraction', 'longest_unchanged_samples', 'known_stall', 'plateau_pc',
            'runtime_variant','completed_flip_timing','navigation_completed','postconditions')
    reasons=[]
    for label,record in (('baseline',before),('candidate',after)):
        if record.get('raw_gs_capture') is not None or record.get('runtime_variant',{}).get('capture_raw_gs',False):
            reasons.append(label+': raw graphics recording invalidates performance comparison')
        if record.get('status')!='observed' or not record.get('navigation_completed') or record.get('navigation_error'):
            reasons.append(label+': unsuccessful run or navigation')
        if record.get('postconditions',{}).get('status')!='passed':
            reasons.append(label+': post-action scene outcome not verified')
        if not record.get('completed_flip_timing',{}).get('window_complete'):
            reasons.append(label+': incomplete timing window')
    report = {'baseline': {k: before.get(k) for k in keys}, 'candidate': {k: after.get(k) for k in keys},
              'same_definition': True, 'gameplay_verified': False, 'local_only': True,
              'eligible_for_timing_comparison':not reasons,'comparison_rejection_reasons':reasons}
    lab.save(output / 'report.json', report)
    rows = '\n'.join(f'| {k} | {before.get(k)} | {after.get(k)} |' for k in keys)
    (output / 'report.md').write_text(
        '# Local scenario comparison\n\nSame scenario definition; gameplay remains unverified.\n\n'+
        f'Timing comparison eligible: {not reasons}. Reasons: {reasons or "none"}.\n\n'+
        '| Observation | Baseline | Candidate |\n| --- | --- | --- |\n' + rows +
        '\n\nBlack-frame ratios and unchanged counters are diagnostics, not rendering-quality or gameplay scores. '
        'Inspect captures locally; timed input can diverge when runtime speed changes.\n')
    print(f'Local comparison: {output / "report.md"}')
    return report


def vu_verification_result(text):
    summaries = [(int(r), int(m)) for r,m in re.findall(r'\[vu1-verify\] (\d+) runs, (\d+) mismatches',text)]
    failures = [int(n) for n in re.findall(r'\[vu1-verify\] mismatch #(\d+)',text)]
    runs = max((r for r,_ in summaries), default=0)
    mismatches = max(failures+[m for _,m in summaries], default=0)
    return {'verified_runs_lower_bound':runs, 'mismatches_lower_bound':mismatches,
            'status':'failed' if mismatches else 'matched_observed_runs' if runs else 'no_compiled_verification_observed'}


def analyze_log(text, known_stalls=None, plateau_samples=8):
    """Cycles advancing alone is not progress; a plateau is only a suspicion."""
    beats = [dict(zip(('pc', 'ra', 'cycles', 'dma', 'gif', 'gs', 'threads'),
                      (int(v, 16) if i < 2 else int(v) for i, v in enumerate(m.groups()))))
             for m in HEARTBEAT.finditer(text)]
    frames = [tuple(map(int, m.groups())) for m in FRAME.finditer(text)]
    longest = run = 0
    plateau_pc = None
    previous = None
    for beat in beats:
        signature = tuple(beat[k] for k in ('pc', 'ra', 'dma', 'gif', 'gs'))
        run = run + 1 if signature == previous else 1
        previous = signature
        if run > longest:
            longest, plateau_pc = run, beat['pc']
    stalled = longest >= plateau_samples
    label = (known_stalls or {}).get(hex(plateau_pc)) if stalled else None
    almost_black = sum(n <= max(1, w * h // 100) for w, h, n in frames)
    return {'heartbeat_samples': len(beats), 'frame_samples': len(frames),
            'almost_black_fraction': almost_black / len(frames) if frames else None,
            'longest_unchanged_samples': longest,
            'suspected_stall': stalled, 'known_stall': label,
            'plateau_pc': hex(plateau_pc) if stalled else None,
            'last_heartbeat': beats[-1] if beats else None,
            'gameplay_verified': False}


def run_scenario(profile, private, definition, name, renderer_worker=None, graphics_history=None):
    import native
    import pipeline as lab
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('Scenario run name must be lowercase letters, digits, hyphens or underscores')
    definition = Path(definition).resolve()
    if not definition.is_relative_to(private.resolve()):
        raise ValueError('Keep game-specific scenario definitions in the private game directory')
    definition_bytes = definition.read_bytes()
    definition_digest = hashlib.sha256(definition_bytes).hexdigest()
    config = json.loads(definition_bytes)
    timing_sequence = config.get('timing_after_sequence')
    if timing_sequence is not None and (type(timing_sequence) is not int or not 1 <= timing_sequence <= 64):
        raise ValueError('Timing sequence must name a controller action between 1 and 64')
    duration = config.get('duration_seconds', 115)
    if not isinstance(duration, (int, float)) or not 1 <= duration <= 600:
        raise ValueError('Scenario duration must be between 1 and 600 seconds')
    plateau_samples = config.get('plateau_samples', 8)
    if not isinstance(plateau_samples, int) or not 2 <= plateau_samples <= 300:
        raise ValueError('plateau_samples must be between 2 and 300')
    stage = json.loads((private / 'analysis/native-build-stage.json').read_text())
    capture_raw = config.get('capture_raw_gs',False)
    raw_seconds = config.get('raw_gs_seconds',145)
    if type(capture_raw) is not bool or type(raw_seconds) not in (int,float) or not 1 <= raw_seconds <= 300:
        raise ValueError('Raw GS capture treatment is invalid')
    if capture_raw and (not stage.get('raw_gs_capture_supported',False) or raw_seconds >= duration):
        raise ValueError('Raw GS capture requires a diagnostic build and a shorter capture than scenario')
    capture_vu = config.get('capture_vu_programs',False)
    vu_execution = config.get('vu_execution')
    if type(capture_vu) is not bool or vu_execution not in (None,'interpreter','compiled','verify'):
        raise ValueError('Vector capture or execution treatment is invalid')
    if (capture_vu or vu_execution is not None) and not stage.get('vu_execution_supported',False):
        raise ValueError('This native build does not support vector capture/compiled execution')
    binary = Path(stage['binary'])
    if lab.digest(binary) != stage['sha256']:
        raise ValueError('Native binary differs from build record')
    allowed = {'PS2_PORT_TEST_START', 'PS2_PORT_TEST_START_ONCE',
               'PS2_PORT_TEST_EXTRA_START_MS', 'PS2_PORT_TEST_PAD_SCRIPT',
               'PS2_PORT_WATCH', 'PS2_PORT_HEAP_TRACE', 'PS2_PORT_MATRIX_TRACE',
               'PS2_PORT_VU_TRACE', 'PS2_PORT_GS_TRACE', 'PS2_PORT_GS_PROFILE',
               'PS2_PORT_TEST_IGNORE_DEPTH', 'PS2_PORT_GS_THREAD', 'PS2_PORT_FRAME_METRICS'}
    settings = config.get('environment', {})
    if set(settings) - allowed or any(not isinstance(v, str) for v in settings.values()):
        raise ValueError('Scenario environment contains an unsupported option or non-string value')
    # Use the same local source review as boot-probe. This does not establish an OS network sandbox.
    for directory in [lab.SOURCE / 'ps2xRuntime/src', lab.SOURCE / 'ps2xIOP/src']:
        for p in directory.rglob('*'):
            if p.suffix in {'.cpp', '.c', '.h', '.hpp', '.inl'} and lab.NETWORK_APIS.search(p.read_text(errors='replace')):
                raise RuntimeError(f'Runtime source requires network/process API review: {p}')
    output = private / 'logs/scenarios' / name
    output.mkdir(parents=True, exist_ok=False)
    frame, log = output / 'latest.ppm', output / 'runner.log'
    env = native.runtime_environment()
    # Diagnostic overrides from an interactive shell must not silently contaminate comparisons.
    for key in list(env):
        if key.startswith(('PS2_PORT_', 'PS2X_')):
            del env[key]
    env.update(settings)
    if capture_raw:
        env['PS2_PORT_RAW_GS_CAPTURE']=str((output/'raw-gs.capture').resolve())
        env['PS2_PORT_RAW_GS_SECONDS']=str(raw_seconds)
    if capture_vu:
        capture_dir=output/'vu-code'
        capture_dir.mkdir(mode=0o700)
        env['PS2X_VU1_CAPTURE']=str(capture_dir.resolve())
    if vu_execution is not None:
        env['PS2X_VU1_RECOMP']='0' if vu_execution=='interpreter' else '1'
        if vu_execution=='verify': env['PS2X_VU1_VERIFY']='1'
    if renderer_worker is not None:
        if renderer_worker not in ('off','on') or 'PS2_PORT_GS_THREAD' in settings:
            raise ValueError('Renderer treatment must be off/on and cannot override a definition setting')
        env['PS2_PORT_GS_THREAD'] = '1' if renderer_worker == 'on' else '0'
    if graphics_history is not None:
        if graphics_history not in ('off','on') or 'PS2_PORT_GS_TRACE' in settings:
            raise ValueError('Graphics history treatment must be off/on and cannot override a definition setting')
        if graphics_history == 'on':
            env['PS2_PORT_GS_TRACE'] = '1'
        else:
            env.pop('PS2_PORT_GS_TRACE', None)
    env.update(PS2_PORT_HEADLESS='1', PS2_PORT_FRAME=str(frame),
               PS2_PORT_IO_ROOT=str((private / 'disc-root').resolve()))
    if 'PS2_PORT_GS_TRACE' in env:
        env['PS2_PORT_GS_HISTORY'] = str(output / 'graphics-history.jsonl')
    navigator = VisualSteps(private, config.get('visual_steps',[]), output / 'pad-command.txt',
                            config.get('post_navigation_actions',[]))
    postconditions=VisualPostconditions(private,config.get('visual_postconditions',[]),
                                       len(navigator.steps)+len(navigator.post_actions))
    if navigator.steps:
        if any(k in settings for k in ('PS2_PORT_TEST_START','PS2_PORT_TEST_PAD_SCRIPT','PS2_PORT_TEST_EXTRA_START_MS')):
            raise ValueError('Visual navigation cannot be combined with host-time button scripts')
        env['PS2_PORT_PAD_COMMAND'] = str(navigator.command)
    captures, last_digest = [], None
    start = time.monotonic()
    outcome = 'timeout'
    verification_offset, verification_tail = 0, ''
    acknowledgement_offset, acknowledgement_tail = 0, ''
    acknowledgements=set()
    with log.open('w') as stream:
        process = subprocess.Popen([str(binary), str(lab.boot_executable(private / 'disc-root', profile))],
                                   cwd=private / 'disc-root', env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            while time.monotonic() - start < duration:
                elapsed=time.monotonic()-start
                # ACK is only command consumption. Postconditions establish the resulting screen.
                with log.open('r',errors='replace') as diagnostics:
                    diagnostics.seek(acknowledgement_offset)
                    chunk=acknowledgement_tail+diagnostics.read()
                    acknowledgement_offset=diagnostics.tell()
                # Only parse complete lines; retain a split ACK until its next write.
                complete,_,acknowledgement_tail=chunk.rpartition('\n')
                acknowledgements.update(int(n) for n in re.findall(
                    r'\[port-lab pad-command\] seq=(\d+)',complete))
                data,token=b'',None
                if frame.exists():
                    before_stat=frame.stat()
                    data = frame.read_bytes()
                    after_stat=frame.stat()
                    if (before_stat.st_mtime_ns,before_stat.st_size)!=(after_stat.st_mtime_ns,after_stat.st_size):
                        data=b''
                    else:
                        token=(after_stat.st_mtime_ns,after_stat.st_size)
                    # Ignore partial writes; preserve a sequence rather than only the final screenshot.
                    parts = data.split(b'\n', 3)
                    if len(parts) == 4 and parts[0] == b'P6' and parts[2] == b'255':
                        try:
                            w, h = map(int, parts[1].split())
                        except ValueError:
                            w = h = 0
                        if w > 0 and h > 0 and len(parts[3]) == w * h * 3:
                            digest = hashlib.sha256(data).hexdigest()
                            if digest != last_digest:
                                target = output / f'frame-{len(captures):04d}.ppm'
                                target.write_bytes(data)
                                captures.append({'seconds': round(time.monotonic() - start, 2),
                                                 'file': target.name, 'sha256': digest})
                                last_digest = digest
                postconditions.update(data,elapsed,acknowledgements,token)
                if not postconditions.blocks_action(len(navigator.events)+1):
                    navigator.update(data,elapsed,acknowledgements)
                if vu_execution == 'verify':
                    with log.open('r',errors='replace') as diagnostics:
                        diagnostics.seek(verification_offset)
                        chunk=verification_tail+diagnostics.read()
                        verification_offset=diagnostics.tell()
                    verification_tail=chunk[-128:]
                    if re.search(r'\[vu1-verify\] mismatch #\d+',chunk):
                        outcome='vu_verification_failed'
                        break
                if process.poll() is not None:
                    outcome = f'exit_{process.returncode}'
                    break
                time.sleep(0.25)
        except TimeoutError as error:
            outcome = 'postcondition_timeout' if postconditions.error else 'navigation_timeout'
            if not postconditions.error:
                navigation_error = str(error)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    log_text = log.read_text(errors='replace')
    acknowledgements = [int(n) for n in re.findall(r'\[port-lab pad-command\] seq=(\d+)', log_text)]
    report = analyze_log(log_text, config.get('known_stalls'), plateau_samples)
    report.update(outcome=outcome, duration_seconds=round(time.monotonic() - start, 2),
                  build_sha256=stage['sha256'], definition_sha256=definition_digest,
                  captures=captures, local_only=True, input_assets={**navigator.assets,**postconditions.assets},
                  postconditions=postconditions.result(),
                  navigation_events=navigator.events, navigation_completed=navigator.completed,
                  navigation_error=locals().get('navigation_error'), input_acknowledgements=acknowledgements,
                  runtime_variant={'renderer_worker':renderer_worker, 'graphics_history':graphics_history,
                                   'capture_vu_programs':capture_vu,'vu_execution':vu_execution,
                                   'capture_raw_gs':capture_raw,'raw_gs_seconds':raw_seconds if capture_raw else None},
                  vu_verification=vu_verification_result(log_text) if vu_execution=='verify' else None,
                  completed_flip_timing=completed_flip_timing(log_text, config.get('timing_after_sequence')))
    if capture_raw:
        import rawgs
        capture_path=output/'raw-gs.capture'
        try:
            result=rawgs.validate(capture_path)
            result.update(file=capture_path.name,sha256=lab.digest(capture_path))
        except (OSError,ValueError) as error:
            result={'status':'invalid','error':str(error),'replayable':False,'local_only':True}
        report['raw_gs_capture']=result
    if not navigator.completed and not report['navigation_error']:
        report['navigation_error'] = 'Navigation incomplete at process exit or run limit'
    if not report['navigation_error'] and any(e['sequence'] not in acknowledgements for e in navigator.events):
        report['navigation_error'] = 'Runtime did not acknowledge every visual input action'
    report['status'] = ('vu_verification_failed' if vu_execution=='verify' and report['vu_verification']['status']=='failed' else
                        'vu_verification_unobserved' if vu_execution=='verify' and report['vu_verification']['status']=='no_compiled_verification_observed' else
                        'navigation_failed' if report['navigation_error'] else
                        'postcondition_failed' if postconditions.steps and not postconditions.completed else
                        'blocked' if report['known_stall'] else
                        'needs_review' if report['suspected_stall'] else
                        'process_exited' if outcome.startswith('exit_') else 'observed')
    if capture_raw and report['raw_gs_capture']['status'] != 'complete':
        report['status']='raw_capture_failed'
    lab.save(output / 'report.json', report)
    (output / 'report.md').write_text(
        f"# Local scenario: {name}\n\nStatus: **{report['status']}**. Gameplay remains unverified.\n\n"
        f"Build: `{stage['sha256'][:16]}`. Outcome: `{outcome}`.\n\n"
        f"Heartbeat samples: {report['heartbeat_samples']}. Longest unchanged run: {report['longest_unchanged_samples']}.\n\n"
        f"Known stall: {report['known_stall'] or 'none detected'}. Almost-black frame fraction: {report['almost_black_fraction']}.\n\n"
        f"Saved {len(captures)} distinct frame captures. Nonblack output does not establish correct imagery or gameplay.\n\n"
        f"Navigation: {len(navigator.events)}/{len(navigator.steps)+len(navigator.post_actions)} recorded actions; {len(acknowledgements)} runtime acknowledgements.\n"
        f"Vector verification: {report['vu_verification'] or 'not requested'}.\n"
        f"Navigation error: {report['navigation_error'] or 'none'}.\n")
    with (output/'report.md').open('a') as stream:
        stream.write(f"\nPostconditions: {report['postconditions']['status']}; "
                     f"{len(postconditions.events)}/{len(postconditions.steps)} verified sampled outcomes.\n")
    print(f"Scenario {name}: {report['status']}; local report: {output / 'report.md'}")
    return report
