"""Isolated A/B regression check: build one title on two lab stacks side by side and compare.

Run before a change to the shared runtime patch stack (patches/*.patch, native.RUNTIME_PATCHES)
or the knowledge bank lands. Each side gets:
  lab/      a copy of that side's lab (its patch set and knowledge bank under test)
  work/     its own work root: a PS2Recomp clone at that lab's pinned revision; only the
            read-only public toolchain, runtime-deps and raylib are shared (symlinks)
  private/  its own private directory: a real disc-root whose boot ELF and memory cards
            (mc0/mc1, kept by the runtime next to the ELF) are copies and whose other game
            data are read-only symlinks, plus a recomp.toml rewritten to this side
Nothing writes to the title's private directory or the shared tool checkout. A passing
check is regression evidence only, never gameplay acceptance.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SIDES = ('baseline', 'candidate')
SHARED_TOOLS = ('toolchain', 'runtime-deps', 'dependencies/raylib')
MEMORY_CARDS = ('mc0', 'mc1')
LAB_IGNORE = shutil.ignore_patterns('.private', '.work', '.git', '__pycache__', '*.pyc', '*.orig')
# Early-exit causes seen in practice (Kelly: "out of memory" after a heap-layout fold).
# Plain mentions such as "IOP MEMORY max allocatable" are not failures.
FATAL_MARKERS = re.compile(r'out of memory|std::bad_alloc|segmentation fault|bus error|abort trap|'
                           r'assertion (?:\S+ )?failed|fatal error', re.I)
# (target, log, timeout seconds, environment): the synthetic suites test-runtime runs.
SUITES = (('ps2PortRuntimeTests', 'runtime-test.log', 10, {'PS2_PORT_GS_THREAD': '0'}),
          ('ps2PortRuntimeTests', 'runtime-worker-test.log', 10, {'PS2_PORT_GS_THREAD': '1'}),
          ('ps2PortPresentationTests', 'host-presentation-test.log', 15, None),
          ('ps2PortHostPadTests', 'host-pad-test.log', 10, None),
          ('ps2PortIopImportTests', 'iop-import-test.log', 15, None),
          ('ps2PortVuTests', 'vu-test.log', 60, None))


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


# ---------------------------------------------------------------- side preparation

def copy_lab(source, destination, patches=None, knowledge=None):
    """Copy a lab tree without private/work data; optionally overlay a patch set or knowledge bank."""
    source, destination = Path(source).resolve(), Path(destination)
    if not (source / 'pipeline.py').is_file() or not (source / 'native.py').is_file():
        raise ValueError(f'Not a PS2 Port Lab tree: {source}')
    shutil.copytree(source, destination, ignore=LAB_IGNORE, symlinks=False)
    if patches is not None:
        overlay = sorted(Path(patches).glob('*.patch'))
        if not overlay:
            raise ValueError(f'Patch overlay has no *.patch files: {patches}')
        for patch in overlay:
            # Accept a recorded applied stack too (.git/port-lab-applied-stack/00-<name>.patch).
            name = re.sub(r'^\d+-', '', patch.name)
            name = name if (destination / 'patches' / name).exists() else patch.name
            shutil.copyfile(patch, destination / 'patches' / name)
    if knowledge is not None:
        shutil.copyfile(knowledge, destination / 'knowledge.json')
    return {'source': str(source), 'patch_overlay': str(patches) if patches else None,
            'knowledge_overlay': str(knowledge) if knowledge else None,
            'patches': {p.name: _digest(p) for p in sorted((destination / 'patches').glob('*.patch'))},
            'knowledge_sha256': _digest(destination / 'knowledge.json') if (destination / 'knowledge.json').exists() else None}


def prepare_work(work, shared_work, shared_source, revision):
    """Own work root: PS2Recomp cloned at the pinned revision; public tools linked read-only."""
    work, shared_work = Path(work), Path(shared_work)
    for name in SHARED_TOOLS:
        target, link = shared_work / name, work / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            link.symlink_to(target.resolve(), target_is_directory=True)
    sse2neon = shared_work / 'build/ps2recomp/_deps/sse2neon-src'
    if sse2neon.exists():
        link = work / 'build/ps2recomp/_deps/sse2neon-src'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(sse2neon.resolve(), target_is_directory=True)
    source = work / 'dependencies/PS2Recomp'
    source.parent.mkdir(parents=True, exist_ok=True)
    # A local clone carries committed objects only, never the shared checkout's applied patches.
    subprocess.run(['git', 'clone', '--quiet', '--no-checkout', str(shared_source), str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--quiet', '--detach', revision], check=True)
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != revision:
        raise RuntimeError(f'PS2Recomp clone is at {head}, not the pinned {revision}')
    return {'work': str(work), 'ps2recomp_revision': head,
            'shared_tools': [n for n in SHARED_TOOLS if (work / n).is_symlink()]}


def stage_disc_root(source, destination, elf_relative):
    """Real disc-root: boot ELF copied, memory cards copied, everything else symlinked read-only.

    Directories on the ELF's path are real so the runtime's elfDirectory/mc0 lands in this copy."""
    source, destination = Path(source).resolve(), Path(destination)
    elf_relative = Path(elf_relative)
    if elf_relative.is_absolute() or '..' in elf_relative.parts or not elf_relative.parts:
        raise ValueError(f'Unsafe boot executable path: {elf_relative}')
    record = {'elf': str(elf_relative), 'linked': 0, 'memory_cards': []}
    current_source, current_destination = source, destination
    current_destination.mkdir(parents=True)
    parts = elf_relative.parts
    for level, part in enumerate(parts):
        final = level == len(parts) - 1
        for entry in sorted(current_source.iterdir()):
            target = current_destination / entry.name
            if entry.name == part:
                continue
            if entry.name in MEMORY_CARDS:
                if not entry.is_dir():
                    raise ValueError(f'Memory card is not a directory: {entry}')
                shutil.copytree(entry, target, symlinks=False)
                record['memory_cards'].append(str(target.relative_to(destination)))
            else:
                target.symlink_to(entry.resolve(), target_is_directory=entry.is_dir())
                record['linked'] += 1
        chosen = current_source / part
        if final:
            if not chosen.is_file():
                raise FileNotFoundError(chosen)
            shutil.copyfile(chosen, current_destination / part)
        else:
            if not chosen.is_dir():
                raise FileNotFoundError(chosen)
            current_source, current_destination = chosen, current_destination / part
            current_destination.mkdir()
    for name in MEMORY_CARDS:  # empty but real, so no first save can follow a link anywhere
        card = current_destination / name
        if not card.exists():
            card.mkdir()
            record['memory_cards'].append(str(card.relative_to(destination)))
    elf = destination / elf_relative
    record['elf_sha256'] = _digest(elf)
    if record['elf_sha256'] != _digest(source / elf_relative):
        raise RuntimeError('Staged boot executable differs from the title copy')
    return record


def rewrite_recomp_config(text, title_private, side_private, elf, generated):
    """Point a title's recomp.toml at this side; no path may still lead into the title's private dir."""
    for old in {str(Path(title_private)), str(Path(title_private).resolve())}:
        text = text.replace(old, str(side_private))
    for key, value in {'input': json.dumps(str(elf)), 'output': json.dumps(str(generated))}.items():
        if re.search(rf'^\s*{key}\s*=', text, re.M):
            text = re.sub(rf'^\s*{key}\s*=.*$', lambda m: f'{key} = {value}', text, count=1, flags=re.M)
        elif '[general]' in text:
            text = text.replace('[general]', f'[general]\n{key} = {value}', 1)
        else:
            raise ValueError('recomp.toml has no [general] section')
    for old in {str(Path(title_private)), str(Path(title_private).resolve())}:
        if old in text:
            raise ValueError('Rewritten recomp.toml still references the title private directory')
    return text


def _scenario_references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'reference' and isinstance(item, str):
                yield item
            elif key == 'reference_alternatives' and isinstance(item, list):
                yield from (x for x in item if isinstance(x, str))
            else:
                yield from _scenario_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scenario_references(item)


def stage_private(profile, title_private, side_directory, side, elf_relative, scenario=None, reuse_generated=False):
    """Build this side's private directory and profile. Only reads the title's private data."""
    title_private = Path(title_private).resolve()
    side_directory = Path(side_directory).resolve()
    private = side_directory / 'private'
    record = {'disc_root': stage_disc_root(title_private / 'disc-root', private / 'disc-root', elf_relative)}
    config = title_private / 'analysis/recomp.toml'
    if not config.is_file():
        raise ValueError('The title has no analysis/recomp.toml; run analyze for it first')
    (private / 'analysis').mkdir(parents=True)
    (private / 'logs').mkdir()
    (private / 'analysis/recomp.toml').write_text(rewrite_recomp_config(
        config.read_text(), title_private, private, private / 'disc-root' / elf_relative, private / 'generated'))
    if profile.get('compiled_vu', False):
        shutil.copytree(title_private / 'generated-vu', private / 'generated-vu', symlinks=False)
    if reuse_generated:
        shutil.copytree(title_private / 'generated', private / 'generated', symlinks=False)
    for item in profile.get('private_source_patches', []):
        relative = Path(item['file'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Unsafe private source patch path: {relative}')
        (private / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(title_private / relative, private / relative)
    if scenario is not None:
        scenario = Path(scenario).resolve()
        if not scenario.is_relative_to(title_private):
            raise ValueError('Scenario definitions live in the title private directory')
        definition = json.loads(scenario.read_text())
        for reference in sorted(set(_scenario_references(definition))):
            relative = Path(reference)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Unsafe scenario reference: {reference}')
            (private / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(title_private / relative, private / relative)
        shutil.copyfile(scenario, private / 'ab-scenario.json')
        record['scenario_sha256'] = _digest(scenario)
    side_profile = dict(profile, id=f'{profile["id"]}-ab-{side}', private_dir=str(private),
                        private_dir_absolute_opt_in=True,
                        notes=f'Isolated A/B {side} copy of {profile["id"]}; regression evidence only.')
    _save(side_directory / 'profile.json', side_profile)
    problems = isolation_problems(private, title_private, elf_relative)
    if problems:
        raise RuntimeError('Side is not isolated: ' + '; '.join(problems))
    return record


def isolation_problems(private, title_private, elf_relative):
    """Every path a run writes (ELF dir, memory cards, translation output) must be this side's own."""
    private, title_private = Path(private), Path(title_private).resolve()
    problems = []
    elf = private / 'disc-root' / elf_relative
    if elf.is_symlink() or not elf.is_file():
        problems.append('boot ELF is not a real copy')
    directory = private / 'disc-root'
    for part in Path(elf_relative).parts[:-1]:
        directory = directory / part
        if directory.is_symlink():
            problems.append(f'{directory} is a link')
    for name in MEMORY_CARDS:
        card = elf.parent / name
        if card.is_symlink() or not card.is_dir():
            problems.append(f'{name} is not a real directory next to the boot ELF')
        elif any(p.is_symlink() for p in card.rglob('*')):
            problems.append(f'{name} contains links')
    text = (private / 'analysis/recomp.toml').read_text()
    output = re.search(r'^\s*output\s*=\s*"([^"]*)"', text, re.M)
    if not output or not Path(output[1]).resolve().is_relative_to(private.resolve()):
        problems.append('recomp.toml output is outside the side private directory')
    for top in ('analysis', 'logs', 'generated', 'generated-vu'):
        path = private / top
        if path.is_symlink() or (path.exists() and path.resolve().is_relative_to(title_private)):
            problems.append(f'{top} leads into the title private directory')
    return problems


# ---------------------------------------------------------------- evidence

def frame_has_image(data, threshold=16, fraction=0.01):
    """True for a complete P6 frame where at least `fraction` of pixels are not near-black."""
    parts = data.split(b'\n', 3)
    if len(parts) != 4 or parts[0] != b'P6' or parts[2] != b'255':
        return False
    try:
        width, height = map(int, parts[1].split())
    except ValueError:
        return False
    pixels = parts[3]
    if width <= 0 or height <= 0 or len(pixels) != width * height * 3:
        return False
    lit = sum(1 for i in range(0, len(pixels), 3) if max(pixels[i:i + 3]) > threshold)
    return lit >= max(1, int(width * height * fraction))


def classify_boot(log_text, returncode, limit_seconds, elapsed_seconds, image_frames):
    """Headless boot gate: alive at the limit, no fatal marker, and some image (the intro) drawn."""
    marker = FATAL_MARKERS.search(log_text or '')
    exited = returncode is not None
    status = ('early_exit' if exited else 'fatal_marker' if marker else
              'no_image' if not image_frames else 'passed')
    return {'status': status, 'passed': status == 'passed', 'returncode': returncode,
            'elapsed_seconds': round(elapsed_seconds, 2), 'limit_seconds': limit_seconds,
            'fatal_marker': marker[0] if marker else None, 'image_frames': image_frames,
            'gameplay_verified': False}


def compare(results):
    """Per-check comparison. A check passing on baseline and failing on candidate is a regression."""
    baseline, candidate = results.get('baseline') or {}, results.get('candidate') or {}
    names = list(dict.fromkeys([*baseline.get('checks', {}), *candidate.get('checks', {})]))
    rows, regressions, improvements = [], [], []
    for name in names:
        a = baseline.get('checks', {}).get(name, {}).get('status', 'missing')
        b = candidate.get('checks', {}).get(name, {}).get('status', 'missing')
        verdict = ('same' if a == b else
                   'regression' if a == 'passed' else
                   'improvement' if b == 'passed' else 'changed')
        rows.append({'check': name, 'baseline': a, 'candidate': b, 'verdict': verdict})
        if verdict == 'regression':
            regressions.append(name)
        elif verdict == 'improvement':
            improvements.append(name)
    complete = bool(baseline.get('finished') and candidate.get('finished'))
    status = 'regression' if regressions else 'incomplete' if not complete else 'no_regression'
    return {'status': status, 'regressions': regressions, 'improvements': improvements,
            'checks': rows, 'gameplay_verified': False}


def render_markdown(report):
    lines = [f'# A/B check: {report["name"]}', '',
             f'Title profile: `{report["profile"]}`. Status: **{report["comparison"]["status"]}**. '
             'Regression evidence only; gameplay remains unverified.', '']
    for side in SIDES:
        info = report['sides'].get(side, {})
        lab = info.get('lab', {})
        lines.append(f'- {side}: lab `{lab.get("source")}`; patches '
                     + ', '.join(f'{n} `{d[:12]}`' for n, d in lab.get('patches', {}).items())
                     + f'; knowledge `{(lab.get("knowledge_sha256") or "none")[:12]}`')
    lines += ['', '| Check | Baseline | Candidate | Verdict |', '|---|---|---|---|']
    lines += [f'| {r["check"]} | {r["baseline"]} | {r["candidate"]} | {r["verdict"]} |'
              for r in report['comparison']['checks']]
    for side in SIDES:
        boot = (report['results'].get(side) or {}).get('checks', {}).get('boot', {})
        if boot.get('fatal_marker') or boot.get('status') == 'early_exit':
            lines.append(f'\n{side} boot: {boot.get("status")} (exit {boot.get("returncode")}, '
                         f'marker {boot.get("fatal_marker")!r}) after {boot.get("elapsed_seconds")} s.')
    return '\n'.join(lines) + '\n'


def busy_runners():
    """PIDs of running ps2EntryRunner processes (a benchmark or GUI session elsewhere)."""
    result = subprocess.run(['pgrep', '-x', 'ps2EntryRunner'], capture_output=True, text=True)
    return [int(p) for p in result.stdout.split()]


# ---------------------------------------------------------------- orchestration

def run_ab_check(profile_path, name, baseline_lab=HERE, candidate_lab=HERE, baseline_patches=None,
                 candidate_patches=None, baseline_knowledge=None, candidate_knowledge=None,
                 scenario=None, boot_seconds=30, reuse_generated=False, output=None,
                 allow_busy=False, jobs=4):
    import pipeline as lab
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('A/B check name must be lowercase letters, digits, hyphens or underscores')
    if not 5 <= boot_seconds <= 300:
        raise ValueError('Boot gate limit must be between 5 and 300 seconds')
    busy = busy_runners()
    if busy and not allow_busy:
        raise RuntimeError(f'ps2EntryRunner is running (pids {busy}); another session may be benchmarking. '
                           'Wait, or pass --allow-busy when timing does not matter.')
    profile, title_private = lab.load_profile(profile_path)
    title_private = title_private.resolve()
    output = Path(output).resolve() if output else lab.WORK / 'ab-checks' / name
    for protected in (title_private, lab.ROOT, lab.SOURCE):
        if output.is_relative_to(protected.resolve()):
            raise ValueError(f'A/B output must be outside {protected}')
    if output.exists():
        raise ValueError(f'A/B output already exists: {output}; choose a new --name')
    elf_relative = lab.boot_executable(title_private / 'disc-root', profile).relative_to(
        (title_private / 'disc-root').resolve())
    overlays = {'baseline': (baseline_lab, baseline_patches, baseline_knowledge),
                'candidate': (candidate_lab, candidate_patches, candidate_knowledge)}
    if len({(str(Path(a).resolve()), str(b), str(c)) for a, b, c in overlays.values()}) == 1:
        raise ValueError('Baseline and candidate are the same stack; pass --baseline-lab, --baseline-patches '
                         'or --baseline-knowledge for the state before the change')
    sides = {}
    output.mkdir(parents=True)
    for side, (source, patches, knowledge) in overlays.items():
        directory = output / side
        directory.mkdir()
        info = {'lab': copy_lab(source, directory / 'lab', patches, knowledge)}
        side_lock = json.loads((directory / 'lab/toolchain.lock.json').read_text())
        info['work'] = prepare_work(directory / 'work', lab.WORK, lab.SOURCE, side_lock['ps2recomp']['revision'])
        info['private'] = stage_private(profile, title_private, directory, side, elf_relative,
                                        scenario, reuse_generated)
        _save(directory / 'side.json', {'side': side, 'lab': str(directory / 'lab'), 'work': str(directory / 'work'),
                                        'profile': str(directory / 'profile.json'),
                                        'shared_build': str(lab.BUILD), 'boot_seconds': boot_seconds,
                                        'reuse_generated': reuse_generated, 'scenario': scenario is not None,
                                        'jobs': jobs})
        sides[side] = info
    if reuse_generated and sides['baseline']['lab']['knowledge_sha256'] != sides['candidate']['lab']['knowledge_sha256']:
        raise ValueError('The knowledge banks differ; --reuse-generated would not exercise them. Retranslate.')
    results = {}
    for side in SIDES:  # one side at a time: each build is heavy
        directory = output / side
        with (directory / 'worker.log').open('w') as log:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', str(directory)],
                           stdout=log, stderr=subprocess.STDOUT)
        result_path = directory / 'result.json'
        results[side] = json.loads(result_path.read_text()) if result_path.exists() else None
    report = {'name': name, 'profile': profile['id'], 'created_at': time.time(), 'output': str(output),
              'sides': sides, 'results': results, 'comparison': compare(results), 'local_only': True}
    _save(output / 'report.json', report)
    (output / 'report.md').write_text(render_markdown(report))
    print(f'A/B check {name}: {report["comparison"]["status"]}; local report: {output / "report.md"}')
    return report


# ---------------------------------------------------------------- per-side worker (subprocess)

def _worker(directory):
    directory = Path(directory).resolve()
    config = json.loads((directory / 'side.json').read_text())
    os.environ['PS2_PORT_LAB_WORK'] = config['work']
    os.environ.pop('PS2_PORT_LAB_EXPECTED_WORK', None)
    # Import this side's lab, never the lab this script was started from.
    sys.path[:] = [config['lab']] + [p for p in sys.path if Path(p or '.').resolve() != HERE]
    import pipeline as lab
    import native
    if lab.ROOT != Path(config['lab']).resolve() or lab.WORK != Path(config['work']).resolve():
        raise RuntimeError(f'Worker imported the wrong lab or work root: {lab.ROOT}, {lab.WORK}')
    profile, private = lab.load_profile(config['profile'])
    if private != (directory / 'private').resolve():
        raise RuntimeError('Side profile resolves to another private directory')
    result = {'side': config['side'], 'lab_root': str(lab.ROOT), 'source': str(lab.SOURCE),
              'runtime_patches': list(native.RUNTIME_PATCHES), 'checks': {}, 'finished': False}
    jobs = str(config.get('jobs', 4))
    cmake = lab.ENV / 'bin/cmake'

    def step(name, function):
        try:
            detail = dict(function() or {})
            result['checks'][name] = {**detail, 'status': detail.get('status', 'passed')}
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            result['checks'][name] = {'status': 'failed', 'error': str(error)}
        _save(directory / 'result.json', result)
        return result['checks'][name]['status'] == 'passed'

    def tools():
        native.apply_runtime_patch()
        if config['reuse_generated']:
            return {'recompiler': 'not built (generated code reused)'}
        shared = Path(config['shared_build']) / '_deps'
        deps = [f'-DFETCHCONTENT_SOURCE_DIR_{n.upper()}={shared / f"{n}-src"}' for n in lab.LOCK.get('resolved_dependencies', {})]
        native.logged([cmake, '-S', lab.SOURCE, '-B', lab.BUILD, '-G', 'Ninja', f'-DCMAKE_MAKE_PROGRAM={lab.ENV / "bin/ninja"}',
                       '-DCMAKE_BUILD_TYPE=Release', '-DPS2X_BUILD_RUNTIME=OFF', '-DPS2X_BUILD_TEST=OFF',
                       '-DPS2X_BUILD_STUDIO=OFF', '-DFETCHCONTENT_FULLY_DISCONNECTED=ON', *deps],
                      private / 'logs/ab-recompiler-configure.log')
        native.logged([cmake, '--build', lab.BUILD, '--target', 'ps2_recomp', '-j', jobs], private / 'logs/ab-recompiler-build.log')
        return {'recompiler': str(lab.BUILD / 'ps2xRecomp/ps2_recomp')}

    def translate():
        if config['reuse_generated']:
            return {'translation': 'reused title generated code (copy)'}
        lab.translate(profile, private)
        return {'translation_stage': json.loads((private / 'analysis/translation-stage.json').read_text()).get('counts')}

    build = private / 'native-build'
    binary = build / 'ps2xRuntime/ps2EntryRunner'

    def native_build():
        vu_directory, vu_record = '', None
        if profile.get('compiled_vu', False):
            from vectors import validated_sources
            vu_directory, vu_record = validated_sources(private)
        command = native.common_configure(build) + [
            '-DFETCHCONTENT_FULLY_DISCONNECTED=ON',
            f'-DCMAKE_PROJECT_INCLUDE={lab.ROOT / "cmake/AttachGame.cmake"}',
            f'-DPS2_PORT_UPSTREAM={lab.SOURCE}', f'-DPS2_PORT_GENERATED_DIR={private / "generated"}',
            f'-DPS2_PORT_TEST_SOURCE={lab.ROOT / "tests/runtime_timer.cpp"}',
            f'-DPS2_PORT_VU_COMPILER_SOURCE={lab.ROOT / "tools/vu1_recomp.cpp"}',
            f'-DPS2_PORT_VU_COMPILED_DIR={vu_directory}', '-DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=16']
        targets = sorted({target for target, *_ in SUITES})
        with native.private_patches_applied(profile, private) as title_patches:
            native.logged(command, private / 'logs/native-configure.log')
            native.logged([cmake, '--build', build, '--target', 'ps2EntryRunner', 'ps2PortVuRecompiler', *targets, '-j', jobs],
                          private / 'logs/native-build.log')
        if not binary.exists():
            raise RuntimeError('Build reported success without the runner binary')
        stage = {'success': True, 'binary': str(binary), 'sha256': lab.digest(binary), 'runtime_verified': False,
                 'built_at': time.time(), 'vu_execution_supported': True, 'compiled_vu': vu_record,
                 'ab_regression_build': True,
                 'runtime_patches': {n: lab.digest(lab.ROOT / n) for n in native.RUNTIME_PATCHES},
                 'private_source_patches': {p.name: lab.digest(p) for p in title_patches}}
        lab.save(private / 'analysis/native-build-stage.json', stage)
        return {'sha256': stage['sha256']}

    def suite(target, log, timeout, env):
        def run():
            outcome = native.logged([build / target], private / 'logs' / log, timeout=timeout, allow_failure=True, env_patch=env)
            if outcome != 'success':
                raise RuntimeError(f'{target}: {outcome}')
        return run

    def boot():
        disc = private / 'disc-root'
        elf = lab.boot_executable(disc, profile)
        out = private / 'logs/ab-boot'
        out.mkdir(parents=True, exist_ok=True)
        frame, log = out / 'frame.ppm', out / 'runner.log'
        env = native.runtime_environment()
        for key in [k for k in env if k.startswith(('PS2_PORT_', 'PS2X_'))]:
            del env[key]
        env.update(PS2_PORT_HEADLESS='1', PS2_PORT_FRAME=str(frame), PS2_PORT_IO_ROOT=str(disc.resolve()))
        limit, start, seen = config['boot_seconds'], time.monotonic(), set()
        with log.open('w') as stream:
            process = subprocess.Popen([str(binary), str(elf)], cwd=disc, env=env, stdout=stream, stderr=subprocess.STDOUT)
            try:
                while time.monotonic() - start < limit and process.poll() is None:
                    if frame.exists():
                        data = frame.read_bytes()
                        if frame_has_image(data):
                            seen.add(hashlib.sha256(data).hexdigest())
                    time.sleep(0.5)
            finally:
                returncode = process.poll()
                if returncode is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        gate = classify_boot(log.read_text(errors='replace'), returncode, limit, time.monotonic() - start, len(seen))
        gate['log'] = str(log)
        return gate  # its status (early_exit, fatal_marker, no_image, passed) is the check status

    def scenario():
        from scenarios import run_scenario
        report = run_scenario(profile, private, private / 'ab-scenario.json', f'ab-{config["side"]}')
        if report['status'] != 'observed':
            raise RuntimeError(f'scenario {report["status"]}')
        return {'scenario_status': report['status']}

    if step('tools', tools) and step('translate', translate) and step('build', native_build):
        for target, log, timeout, env in SUITES:
            step(log.removesuffix('.log'), suite(target, log, timeout, env))
        step('boot', boot)
        if config['scenario']:
            step('scenario', scenario)
    result['finished'] = True
    _save(directory / 'result.json', result)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        _worker(sys.argv[2])
    else:
        raise SystemExit('Use: run.py --target macos --profile <profile> ab-check ... (see ENGINE.md)')
