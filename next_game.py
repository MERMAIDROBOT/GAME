"""Offline next-title intake/translation runner with validated resume and tickets.

Only fixed pipeline subcommands execute. Recipes cannot provide shell commands.
Native clean-build consolidation is a separate, explicitly unfinished gate.
"""
from pathlib import Path
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import pipeline as lab

STAGES = [('verify', 'verification.json'), ('inspect', 'analysis/elf.json'),
          ('knowledge-plan', 'analysis/knowledge-plan.json'),
          ('analyze', 'analysis/analyzer-stage.json'), ('translate', 'analysis/translation-stage.json'),
          ('check-generated', 'analysis/native-syntax-stage.json')]


def create_profile(path, title, source):
    """Discover the boot executable and pin its revision before importing."""
    path, source = Path(path).resolve(), Path(source).resolve()
    if path.exists(): raise ValueError('Profile exists; do not replace another title')
    identity = path.stem
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', identity) or not title.strip():
        raise ValueError('Choose a lowercase profile filename and a nonempty title')
    profile = dict(id=identity, title=title, runtime_status='not_integrated')
    elf = lab.boot_executable(source, profile)
    facts = lab.elf_info(elf)
    profile.update(expected_executable=str(elf.relative_to(source)), expected_sha256=facts['sha256'])
    lab.save(path, profile)
    return profile


def inventory(private):
    """Cheap metadata census; does not guess import coverage from IRX filenames."""
    root = Path(private)/'disc-root'
    modules = []
    containers = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink(): continue
        if path.suffix.lower() == '.irx':
            modules.append({'path':str(path.relative_to(root)), 'sha256':lab.digest(path), 'bytes':path.stat().st_size})
        suffix = path.suffix.lower() or '(none)'
        containers[suffix] = containers.get(suffix, 0)+1
    return dict(iop_modules=modules, file_extensions=containers,
                import_coverage='not inferred from filenames; per-module audit required')


def outputs(private, command, artifact):
    paths = [Path(private)/artifact]
    if command == 'analyze': paths.append(Path(private)/'analysis/recomp.toml')
    if command == 'translate':
        if (Path(private)/'analysis/knowledge-translation.json').exists():
            paths.append(Path(private)/'analysis/knowledge-translation.json')
        paths.extend(sorted((Path(private)/'generated').rglob('*')))
    return {str(p.relative_to(private)):lab.digest(p) for p in paths if p.is_file()}


def run(profile_path, name, source=None, through='intake', execute=False, resume=False, runner=None, process_audit=True):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name): raise ValueError('Invalid run name')
    if through not in {'intake', 'translate'}: raise ValueError('Unknown automation target')
    profile, private = lab.load_profile(profile_path)
    private.mkdir(parents=True, exist_ok=True)
    target = private/'next-game-runs'/name
    if target.exists() and not resume: raise ValueError('Run already exists; use --resume or a new name')
    target.mkdir(parents=True, exist_ok=True)
    report_path = target/'report.json'
    previous = json.loads(report_path.read_text()) if report_path.exists() else None
    disc = Path(source).resolve() if source else private/'disc-root'
    elf = lab.boot_executable(disc, profile)
    identity = lab.digest(elf)
    # Pin all reusable implementation files so edited tooling cannot reuse old results.
    engine = {p:lab.digest(lab.ROOT/p) for p in lab.SOURCE_FILES}
    tools = {str(p): lab.digest(p) if p.is_file() else None for p in
             [lab.BUILD/'ps2xAnalyzer/ps2_analyzer', lab.BUILD/'ps2xRecomp/ps2_recomp']}
    contract = dict(profile_sha256=lab.digest(profile_path), executable_sha256=identity,
                    implementation=engine, tools=tools, work=str(lab.WORK), source=str(disc.resolve()), through=through)
    if previous and previous['contract'] != contract:
        raise ValueError('Resume input/profile/tooling changed; start a new run')
    stages = ([('import', 'disc-manifest.json')] if source else []) + STAGES[:3 if through == 'intake' else 6]
    plan = [{'command':cmd, 'artifact':artifact} for cmd, artifact in stages]
    report = dict(schema=1, local_only=True, title_id=profile['id'], contract=contract,
        plan=plan, stages=(previous or {}).get('stages', []), status='planned',
        playable_certified=False, clean_rebuild_verified=False,
        remaining_gates=['instruction and SDK/IOP import coverage', 'disjoint guest/runtime memory',
            'consolidated clean native build from current shared source',
            'renderer state/CLUT/readback and audio/movie conformance',
            'confirmed player navigation, controls and save/load',
            'original-speed and unique-frame pacing evidence', 'enhancement A/B and app packaging'])
    lab.save(report_path, report)
    if not execute: return report
    lock_path = lab.WORK/'next-game.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another next-game run owns the pipeline slot')
        if runner is None and process_audit:
            # Fail closed if process visibility is unavailable. Never stop another worker.
            active = subprocess.check_output(['/bin/ps', '-axo', 'comm='], text=True)
            conflicts = [p for p in active.splitlines() if any(Path(p.strip()).name == n for n in
                ['ps2EntryRunner', 'ninja', 'realesrgan-ncnn-vulkan', 'ps2_recomp', 'ps2_analyzer'])]
            if conflicts: raise RuntimeError('Conflicting game/build/GPU task is active; retry when idle')
        upstream_changed = False
        done = {s['command']:s for s in report['stages']}
        mapped={'verify':'disc-integrity','inspect':'binary-inspection',
                'knowledge-plan':'knowledge-contracts','translate':'ee-translation'}
        for command, artifact in stages:
            old = done.get(command)
            valid = old and old['status'] == 'passed' and old.get('outputs') and outputs(private, command, artifact) == old['outputs']
            # A fresh verification rehashes the actual disc tree on every resume.
            if valid and not upstream_changed and command not in {'import', 'verify'}:
                if command in mapped:
                    from enhancements import record_stage
                    record_stage(profile,private,mapped[command],artifact)
                continue
            step = dict(command=command, status='running', started=time.time())
            report['stages'] = [s for s in report['stages'] if s['command'] != command]
            report['stages'].append(step)
            report['status'] = 'running'; lab.save(report_path, report)
            try:
                arguments = [sys.executable, str(lab.ROOT/'pipeline.py'), '--profile', str(Path(profile_path).resolve()), command]
                if command == 'import': arguments.append(str(Path(source).resolve()))
                log = target/(command+'.log')
                if runner:
                    runner(command, private)
                else:
                    # Strip game/runtime toggles, but keep this run's pinned tool/work root:
                    # child stages must use the same tools the resume contract recorded.
                    env = {k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
                    env['PS2_PORT_LAB_WORK'] = env['PS2_PORT_LAB_EXPECTED_WORK'] = str(lab.WORK)
                    # Download/setup commands are intentionally absent from this workflow.
                    with log.open('w') as stream:
                        subprocess.run(arguments, stdout=stream, stderr=subprocess.STDOUT, env=env, check=True)
                if not (private/artifact).is_file(): raise RuntimeError('Stage did not produce its required artifact')
                if command == 'translate':
                    stage = json.loads((private/artifact).read_text())
                    counts = stage.get('counts', {})
                    if counts.get('errors', 0) or counts.get('decode_failures', 0):
                        raise RuntimeError('Translation reports errors/decode failures; resolve instruction coverage before proceeding')
                    if stage.get('accepted') is not True:
                        raise RuntimeError('Translation not accepted: ' + '; '.join(stage.get('blockers') or ['missing acceptance record']))
                if command in mapped:
                    from enhancements import record_stage
                    record_stage(profile,private,mapped[command],artifact)
                step.update(status='passed', outputs=outputs(private, command, artifact), finished=time.time())
                # Repeating identical verify/import should not invalidate the expensive translator output.
                if command != 'verify' and (not valid or step['outputs'] != old.get('outputs')):
                    upstream_changed = True
            except Exception as error:
                step.update(status='failed', error=str(error), finished=time.time())
                report['status']='blocked'
                report['ticket']=dict(subsystem=command, symptom=str(error),
                    reproducer=arguments, log=str(target/(command+'.log')),
                    next_action='Inspect this local failure; fix shared contract or add a revision-gated title adapter. Resume this run after review.')
                lab.save(report_path, report)
                return report
            lab.save(report_path, report)
        report['inventory'] = inventory(private)
        from enhancements import schedule
        graph=schedule(profile,private)
        report['enhancement_stages']={'qualified':graph['completed'],'total':graph['total'],
                                     'report':str(private/'analysis/enhancement-engine-plan.json')}
        report['status'] = 'intake_complete_integration_required' if through == 'intake' else 'translation_complete_integration_required'
        report['translation_counts'] = json.loads((private/'analysis/translation-stage.json').read_text()).get('counts', {}) if through == 'translate' else None
        lab.save(report_path, report)
    return report
