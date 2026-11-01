"""Offline next-title intake/translation runner with validated resume and tickets.

Only fixed pipeline subcommands execute. Recipes cannot provide shell commands.
Native clean-build consolidation is a separate, explicitly unfinished gate.
"""
from pathlib import Path
import fcntl
import hashlib
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


def native_artifact_outputs(private,command):
    """Resume verifies real products and inputs, not just a passed JSON receipt."""
    private=Path(private).resolve();result={}
    def pinned(path,expected,kind,inside=True,root=None):
        p=Path(path)
        boundary=Path(root).resolve() if root is not None else (private if inside else None)
        if p.is_symlink() or (boundary is not None and not p.resolve().is_relative_to(boundary)) or not p.is_file():
            raise ValueError('Missing/unsafe '+kind+' artifact')
        digest=lab.digest(p)
        if digest!=expected:raise ValueError('Changed '+kind+' artifact')
        result[kind+':'+str(p.resolve())]=digest
    if command=='build-native':
        stage=json.loads((private/'analysis/native-build-stage.json').read_text())
        pinned(stage['binary'],stage['sha256'],'native-runner')
        for group in ['frameworks','graph_tests']:
            for name,record in stage.get(group,{}).items():pinned(record['path'],record['sha256'],group+'-'+name)
        abi=stage.get('abi_layouts_receipt')
        if abi is not None:
            pinned(abi.get('path',''),abi.get('sha256'),'source-abi-receipt')
            pinned(abi.get('config',''),abi.get('config_sha256'),'source-abi-config')
            if json.loads(Path(abi['path']).read_text()).get('status')!='abi_layouts_match':raise ValueError('Source ABI gate did not pass')
        if stage.get('source_graph_recipe_sha256'):
            pinned(stage.get('source_graph_recipe',''),stage['source_graph_recipe_sha256'],'source-recipe')
            pinned(stage.get('source_graph_staging',''),stage.get('source_graph_staging_sha256'),'source-staging')
            from clean_build import inspect
            checked=inspect(stage['source_graph_recipe'])
            result['source-inventory-sha256']=checked['recipe_sha256']
        from native_graph import verify_source_chain
        verify_source_chain(stage,private)
        for key in ['source_corrections','translation_rebase']:
            if stage.get(key) is not None:
                entry=stage[key];pinned(private/entry['path'],entry['sha256'],key.replace('_','-'))
    if command=='test-runtime':
        record=private/'analysis/native-build-stage.json'
        stage=json.loads(record.read_text()) if record.exists() else {}
        if stage.get('source_graph_recipe_sha256'):
            from native import verify_graph_conformance
            verify_graph_conformance(stage,private)
    if command=='package-local':
        stage=json.loads((private/'analysis/local-package-stage.json').read_text())
        app=Path(stage['app']).absolute()
        if app.parent.resolve()!=lab.ROOT.parent.resolve() or app.suffix!='.app' or app.is_symlink():raise ValueError('Packaged app is outside the engine output directory')
        for relative,key in [('Contents/MacOS/ps2EntryRunner','build_sha256'),('Contents/MacOS/LocalLauncher','launcher_sha256'),('Contents/Info.plist','info_sha256'),('Contents/Resources/RunConfiguration.plist','configuration_sha256')]:
            pinned(app/relative,stage.get(key),'app-'+key,inside=False,root=app)
        for name,digest in stage.get('frameworks',{}).items():
            if Path(name).name!=name:raise ValueError('Unsafe packaged library name')
            pinned(app/'Contents/Frameworks'/name,digest,'app-library-'+name,inside=False,root=app)
        from launch_config import directory_pin
        from clean_build import path_in
        for key,record in stage.get('launch_assets',{}).items():
            path=Path(record['path']).resolve()
            if not path.is_relative_to(private):raise ValueError('Launch assets escaped private directory')
            path=path_in(private,str(path.relative_to(private)))
            current=directory_pin(path)
            if current['sha256']!=record['sha256']:raise ValueError('Packaged launch assets changed')
            result['launch-assets-'+key]=current['sha256']
    return result


def outputs(private, command, artifact):
    paths = [] if command=='build-native' else [Path(private)/artifact]
    if command == 'analyze': paths.append(Path(private)/'analysis/recomp.toml')
    if command == 'translate':
        if (Path(private)/'analysis/knowledge-translation.json').exists():
            paths.append(Path(private)/'analysis/knowledge-translation.json')
        if (Path(private)/'analysis/port-qualification.json').is_file():
            paths.append(Path(private)/'analysis/port-qualification.json')
        paths.extend(sorted((Path(private)/'generated').rglob('*')))
    result={str(p.relative_to(private)):lab.digest(p) for p in paths if p.is_file()}
    if command=='build-native':
        # Runtime conformance owns these two mutable fields; it has its own
        # pinned receipt and must not invalidate an unchanged source build.
        stage=json.loads((Path(private)/artifact).read_text())
        contract={k:v for k,v in stage.items() if k not in {'runtime_verified','conformance_receipt'}}
        result['native-build-contract:'+artifact]=hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result.update(native_artifact_outputs(private,command))
    return result


def run(profile_path, name, source=None, through='intake', execute=False, resume=False, runner=None, process_audit=True, image=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name): raise ValueError('Invalid run name')
    if through not in {'intake', 'translate','native','package'}: raise ValueError('Unknown automation target')
    profile, private = lab.load_profile(profile_path)
    private.mkdir(parents=True, exist_ok=True)
    target = private/'next-game-runs'/name
    if target.exists() and not resume: raise ValueError('Run already exists; use --resume or a new name')
    target.mkdir(parents=True, exist_ok=True)
    report_path = target/'report.json'
    previous = json.loads(report_path.read_text()) if report_path.exists() else None
    if execute and through!='intake' and runner is None:
        # Refresh before pinning the parent run contract, but retain failures
        # even when no translation contract could be established yet.
        from tool_state import ensure
        preflight = dict(schema=1,status='running',stage='compiler-refresh',
                         work=str(lab.WORK),toolchain=str(lab.ENV),started=time.time(),
                         playable_certified=False)
        preflight_path=target/'compiler-preflight.json'
        lab.save(preflight_path,preflight)
        try:
            ensure(lab)
        except Exception as error:
            preflight.update(status='blocked',error=str(error),finished=time.time())
            lab.save(preflight_path,preflight)
            raise
        preflight.update(status='ready',finished=time.time())
        lab.save(preflight_path,preflight)
    disc = Path(source).resolve() if source else private/'disc-root'
    elf = lab.boot_executable(disc, profile)
    identity = lab.digest(elf)
    # Pin all reusable implementation files so edited tooling cannot reuse old results.
    engine = {p:lab.digest(lab.ROOT/p) for p in lab.SOURCE_FILES}
    tools = {str(p): lab.digest(p) if p.is_file() else None for p in
             [lab.BUILD/'ps2xAnalyzer/ps2_analyzer', lab.BUILD/'ps2xRecomp/ps2_recomp']}
    contract = dict(profile_sha256=lab.digest(profile_path), executable_sha256=identity,
                    implementation=engine, tools=tools, work=str(lab.WORK), toolchain=str(lab.ENV), source=str(disc.resolve()), through=through)
    if image:
        contract['image'] = str(Path(image).resolve())
    if previous and previous['contract'] != contract:
        raise ValueError('Resume input/profile/tooling changed; start a new run')
    # Titles that read ISO9660 by LBN need a sector image as well as the file tree.
    stages = (([('import', 'disc-manifest.json')] if source else []) +
              ([('import-image', 'analysis/cd-image-stage.json')] if image else []) + STAGES[:3 if through == 'intake' else 6])
    if through in {'native','package'}:
        stages += [('build-native','analysis/native-build-stage.json'),('test-runtime','analysis/runtime-tests-stage.json')]
    if through=='package':
        stages += [('package-local','analysis/local-package-stage.json')]
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
            from compute_slot import assert_idle
            assert_idle()
        upstream_changed = False
        done = {s['command']:s for s in report['stages']}
        mapped={'verify':'disc-integrity','inspect':'binary-inspection',
                'knowledge-plan':'knowledge-contracts','translate':'ee-translation'}
        for command, artifact in stages:
            old = done.get(command)
            try:
                valid = old and old['status'] == 'passed' and old.get('outputs') and outputs(private, command, artifact) == old['outputs']
            except (ValueError,KeyError,OSError):
                valid = False # A stale binary/library/source pin forces the owning stage to rerun.
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
                if command == 'import-image': arguments.append(str(Path(image).resolve()))
                log = target/(command+'.log')
                if runner:
                    runner(command, private)
                else:
                    # Strip game/runtime toggles, but keep this run's pinned tool/work root:
                    # child stages must use the same tools the resume contract recorded.
                    env = {k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
                    env['PS2_PORT_LAB_WORK'] = env['PS2_PORT_LAB_EXPECTED_WORK'] = str(lab.WORK)
                    env['PS2_PORT_LAB_TOOLCHAIN'] = str(lab.ENV)
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
                if command == 'translate':
                    from port_checks import audit
                    qualification=audit(generated=private/'generated')
                    lab.save(private/'analysis/port-qualification.json',qualification)
                    if not qualification['structural_checks_passed']:
                        raise RuntimeError('Generated code failed automatic port qualification; see analysis/port-qualification.json')
                if command in mapped:
                    from enhancements import record_stage
                    record_stage(profile,private,mapped[command],artifact)
                step.update(status='passed', outputs=outputs(private, command, artifact), finished=time.time())
                # Repeating identical verify/import should not invalidate the expensive translator output.
                # The sector image does not feed translation.
                if command not in {'verify', 'import-image'} and (not valid or step['outputs'] != old.get('outputs')):
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
        report['status'] = {'intake':'intake_complete_integration_required','translate':'translation_complete_integration_required','native':'native_complete_gameplay_unverified','package':'package_complete_gameplay_unverified'}[through]
        report['translation_counts'] = json.loads((private/'analysis/translation-stage.json').read_text()).get('counts', {}) if through != 'intake' else None
        lab.save(report_path, report)
    return report
