"""Executable port qualification from actual artifacts, sources and runtime logs.

Checks do not launch a game or equate counters with visual/gameplay acceptance.
"""
from pathlib import Path
import hashlib
import json
import plistlib
import re


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()



def renderer_paths(app, config):
    """Resolve the declared bundled producer, retaining legacy split-renderer checks."""
    root=Path(app).resolve()/'Contents/Frameworks'
    bindings=config.get('FrameworkEnvironment',{})
    if not isinstance(bindings,dict):raise ValueError('FrameworkEnvironment must be a mapping')
    producer=bindings.get('PS2_PORT_PRIVATE_GS_PLUGIN')
    result=[]
    if producer is not None:
        if not isinstance(producer,str) or not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',producer):
            raise ValueError('Invalid bundled renderer filename')
        result.append(root/producer)
    legacy=root/'libclaude-hwgs.dylib'
    if legacy.is_file() or producer is None:result.append(legacy)
    return result


def inspect_app(app):
    app = Path(app).resolve()
    findings = []
    def flag(code, detail, severity='error'):
        findings.append(dict(code=code, severity=severity, detail=detail))
    path = app/'Contents/Resources/RunConfiguration.plist'
    if not path.is_file():
        return [dict(code='configuration_missing', severity='error', detail=str(path))]
    config = plistlib.loads(path.read_bytes())
    try:
        from bundle_assets import resolve
        config=resolve(app,config)
    except (ValueError,TypeError,KeyError) as error:
        flag('bundled_configuration_invalid',str(error));return findings
    disc = Path(config.get('DiscDirectory', ''))
    executable = Path(config.get('Executable', ''))
    if not disc.is_absolute() or not disc.is_dir():
        flag('disc_missing', 'DiscDirectory must be an existing absolute directory')
    else:
        # CDVD's recursive discovery skips directory symlinks. Do not resolve
        # them away before checking: that hid the missing speech/music root.
        links = [str(disc)] if disc.is_symlink() else []
        links += [str(p) for p in disc.rglob('*') if p.is_symlink() and p.is_dir()]
        if links:
            flag('streaming_directory_symlinks', links)
    if not executable.is_absolute() or not executable.is_file():
        flag('executable_missing', 'Executable must be an existing absolute file')
    image = config.get('CdImage')
    if image is not None and (not isinstance(image,str) or not Path(image).is_absolute() or Path(image).is_symlink() or not Path(image).is_file()):
        flag('disc_image_missing', 'CdImage must be an existing absolute regular file')
    env = config.get('Environment', {})
    for key in ('PS2_PORT_HEADLESS', 'PS2_PORT_PRIVATE_WINDOW_CONTROL', 'PS2_PORT_FRAME',
                'PS2_PORT_TEST_PAD_SCRIPT', 'PS2_PORT_DIAGNOSTIC_SECONDS'):
        if str(env.get(key, '')).lower() not in ('', '0', 'false', 'off'):
            flag('diagnostic_release_setting', key)
    pack = env.get('PS2_PORT_TEX_PACK')
    if pack and not Path(pack).is_dir():
        flag('texture_pack_missing', str(pack))
    try:
        libraries=renderer_paths(app,config)
    except ValueError as error:
        flag('renderer_binding_invalid',str(error));libraries=[]
    for library in libraries:
        if library.is_symlink() or not library.is_file():
            declared=bool(config.get('FrameworkEnvironment',{}).get('PS2_PORT_PRIVATE_GS_PLUGIN'))
            flag('renderer_not_inventoried',str(library),'error' if declared else 'warning')
    return findings


def audit(*, app=None, receipt=None, log=None, generated=None, acceptance=None, gif=None, required_scenarios=None, expected_audio_wavs=None):
    findings = []
    inputs = {}
    required = [] if required_scenarios is None else required_scenarios
    scenario_id = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
    if (not isinstance(required, list) or len(required) > 128 or
        any(not isinstance(item, str) or not scenario_id.fullmatch(item) for item in required) or
        len(required) != len(set(required))):
        raise ValueError('Required scenarios must be distinct nonempty identifiers (maximum 128)')
    if app:
        findings += inspect_app(app)
        config = Path(app)/'Contents/Resources/RunConfiguration.plist'
        if config.is_file(): inputs['configuration'] = sha(config)
    if receipt:
        if not app: raise ValueError('Artifact receipt requires --app')
        data = json.loads(Path(receipt).read_text())
        inputs['receipt'] = sha(receipt)
        root = Path(app).resolve()/'Contents'
        files = data.get('files', {})
        if not isinstance(files, dict) or not files:
            raise ValueError('Receipt must contain nonempty files mapping relative to Contents')
        for rel, expected in files.items():
            path = root/rel
            if not path.resolve().is_relative_to(root) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Unsafe artifact receipt entry')
            if not path.is_file() or sha(path) != expected:
                findings.append(dict(code='artifact_mismatch', severity='error', detail=rel))
        config=plistlib.loads((root/'Resources/RunConfiguration.plist').read_bytes())
        try: libraries=renderer_paths(app,config)
        except ValueError: libraries=[] # inspect_app already reports the invalid declaration
        for library in libraries:
            if library.is_file() and str(library.relative_to(root)) not in files:
                findings.append(dict(code='renderer_receipt_missing',severity='error',detail=str(library.relative_to(root))))
    if generated:
        from branches import rewrite
        root = Path(generated).resolve()
        if not root.is_dir(): raise ValueError('Generated source directory does not exist')
        from pipeline import generated_sources
        for path in generated_sources(root):
            try:
                _, count, changes = rewrite(path.read_text())
                if changes:
                    findings.append(dict(code='ee_branch_width', severity='error', file=str(path.relative_to(root)), instructions=changes))
            except ValueError as error:
                findings.append(dict(code='ee_branch_shape_unknown', severity='error', file=str(path.relative_to(root)), detail=str(error)))
    if generated:
        from sdk_policy import audit_generated
        findings += [dict(code='stateful_sdk_stub',severity='error',**r) for r in audit_generated(generated)['findings']]
    packet_report=None
    if gif:
        from gif_packets import inspect
        path=Path(gif)
        if path.stat().st_size>128*1024*1024: raise ValueError('GIF stream exceeds 128 MiB')
        inputs['gif']=sha(path)
        packet_report=inspect(path.read_bytes())
    metrics = {}
    if expected_audio_wavs is not None:
        if not isinstance(expected_audio_wavs,list) or not 1<=len(expected_audio_wavs)<=16:
            raise ValueError('Provide1..16explicit expected-audible WAV files')
        from audio_evidence import inspect as inspect_audio
        records=[inspect_audio(path) for path in expected_audio_wavs]
        inputs['expected_audio_wavs']=[r['sha256'] for r in records]
        metrics['captured_audio']=records
        for index,record in enumerate(records):
            if record['entirely_silent']:
                findings.append(dict(code='expected_audio_entirely_silent',severity='error',capture=index,sha256=record['sha256']))
    if log:
        path = Path(log)
        if path.stat().st_size > 128 * 1024 * 1024: raise ValueError('Log exceeds 128 MiB limit')
        text = path.read_text(errors='replace'); inputs['log'] = sha(path)
        for label, pattern in {
            'audio_missing_frames': r'(\d+) missing frames',
            'audio_underruns': r'(\d+) underruns',
            'audio_dropped_frames': r'(\d+) dropped frames',
            'texture_load_failures': r'load_failures=(\d+)',
            'field_peak_ms': r'peak_ms=([\d.]+)',
        }.items():
            values = [float(v) for v in re.findall(pattern, text)]
            if values:
                metrics[label] = max(values)
                if metrics[label] > (33.34 if label == 'field_peak_ms' else 0):
                    findings.append(dict(code=label, severity='warning' if label == 'field_peak_ms' else 'error', maximum=metrics[label]))
        unimplemented=re.findall(r"(?:Unimplemented PS2 stub called\. name=|Unimplemented PS2 stub called: )([A-Za-z_][A-Za-z_0-9]*)",text)
        if unimplemented:
            metrics['unimplemented_sdk_diagnostics']=len(unimplemented)
            findings.append(dict(code='sdk_handler_unimplemented',severity='error',
                symbols=sorted(set(unimplemented)),observations=len(unimplemented)))
        from feedback import NATIVE,WEB
        missing=sorted({int(match[1],16) for pattern in (NATIVE,WEB) for match in pattern.finditer(text)})
        if missing:
            metrics['missing_guest_target_count']=len(missing)
            findings.append(dict(code='guest_target_missing',severity='error',
                addresses=[f'{address:08x}' for address in missing]))
        allocation_failures=re.findall(r'\[port-lab alloc\] mode=(\d+) bytes=(\d+) fixed=[0-9a-fA-Fx]+ result=(?:0x)?0+\b free=(\d+)',text)
        allocation_failures=[dict(mode=int(mode),requested_bytes=int(size),free_bytes=int(free)) for mode,size,free in allocation_failures if int(size)>0]
        if allocation_failures:
            metrics['iop_allocation_failure_count']=len(allocation_failures)
            findings.append(dict(code='iop_allocation_failed',severity='error',requests=allocation_failures))
        # Decoder errors can coexist with clean audio/frame counters. Detect
        # actual media-library diagnostics, not arbitrary game text about errors.
        decoder_errors=[]
        for line in text.splitlines():
            if re.search(r'\[(?:mpeg[124]?video|h264|hevc|mp[123](?:float)?|aac|ac3|pcm_[a-z0-9_]+) @ [^\]]+\]',line,re.I) and re.search(
                    r'\b(?:invalid (?:mb type|cbp|data)|ac-tex damaged|mb incr damaged|error while decoding|error decoding|corrupt(?:ed)? (?:frame|packet)|concealing \d+ .* errors)\b',line,re.I):
                decoder_errors.append(line[:500])
        if decoder_errors:
            metrics['media_decode_error_count']=len(decoder_errors)
            findings.append(dict(code='media_decode_error',severity='error',observations=len(decoder_errors),examples=decoder_errors[:8]))
        if '[native-surface acquired]' in text:
            from cadence import analyze_surface_acquisitions
            metrics['surface_acquisitions']=analyze_surface_acquisitions(text)
        blocked=re.findall(r'\[IOP\] RPC server 0x([0-9a-fA-F]+) still blocked after (\d+) cycles',text)
        if blocked:
            metrics['iop_rpc_blocked_count']=len(blocked)
            findings.append(dict(code='iop_rpc_blocked',severity='error',
                servers=sorted(set('0x'+address.lower() for address,_ in blocked)),
                occurrences=len(blocked),maximum_wait_cycles=max(int(cycles) for _,cycles in blocked)))
        hits = re.findall(r'\[residency-cache\] hits=(\d+) misses=(\d+)', text)
        if hits:
            h, m = sum(int(a) for a,b in hits), sum(int(b) for a,b in hits)
            metrics['residency_query_avoidance'] = h/(h+m) if h+m else None
    # Human/visual acceptance remains unknown unless evidence names this exact
    # configuration AND all executable/renderer artifacts in the supplied receipt.
    accepted_scenarios = []
    if acceptance:
        if not app or not receipt: raise ValueError('Acceptance needs app and pinned receipt')
        data = json.loads(Path(acceptance).read_text())
        pin = sha(receipt)
        if data.get('receipt_sha256') != pin: raise ValueError('Acceptance targets a different artifact receipt')
        # A renderer-only receipt must not attest a different launcher, runner,
        # framework or configuration. Every executable component needs a pin.
        critical = ['Resources/RunConfiguration.plist', 'Info.plist']
        native_files = []
        for directory in ('MacOS', 'Frameworks'):
            base = root/directory
            if base.is_dir():
                native_files += [path for path in base.rglob('*') if path.is_file() or path.is_symlink()]
        if not any(path.is_relative_to(root/'MacOS') for path in native_files):
            findings.append(dict(code='acceptance_native_runner_missing', severity='error'))
        critical += [str(path.relative_to(root)) for path in native_files]
        for rel in critical:
            path = root/rel
            if path.is_symlink() or not path.is_file() or rel not in files:
                findings.append(dict(code='acceptance_artifact_unpinned', severity='error', detail=rel))
        scenarios = data.get('scenarios', [])
        if not isinstance(scenarios, list) or len(scenarios) > 128:
            raise ValueError('Acceptance scenarios must be a bounded list')
        seen = set()
        for item in scenarios:
            if not isinstance(item, dict) or item.get('status') not in ('passed','failed','unverified'):
                raise ValueError('Invalid scenario acceptance')
            name = item.get('id')
            if not isinstance(name, str) or not scenario_id.fullmatch(name) or name in seen:
                raise ValueError('Acceptance scenario identifiers must be valid and distinct')
            seen.add(name)
            evidence = Path(item.get('evidence', ''))
            if not evidence.is_absolute(): evidence = Path(acceptance).resolve().parent/evidence
            if item['status']=='passed':
                if not evidence.is_file() or sha(evidence) != item.get('evidence_sha256'):
                    raise ValueError('Scenario evidence missing or changed')
                accepted_scenarios.append(item['id'])
            else:
                findings.append(dict(code='scenario_'+item['status'], severity='error', detail=item.get('id')))
    missing = sorted(set(required) - set(accepted_scenarios))
    if missing:
        findings.append(dict(code='required_scenarios_unverified', severity='error', scenarios=missing))
    clean = not any(f['severity']=='error' for f in findings)
    return dict(schema=1, findings=findings, metrics=metrics, inputs=inputs,
                structural_checks_passed=clean,
                required_scenarios=required, missing_scenarios=missing,
                declared_acceptance_complete=bool(required) and not missing and clean,
                accepted_scenarios=accepted_scenarios, gif=packet_report,
                gameplay_verified=False,
                coverage='Reported scenarios only; missing audio or timing counters remain unknown. Field timings and cache hits are not unique FPS.')
