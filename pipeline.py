#!/usr/bin/env python3
"""Local, resumable disc-to-C++ orchestration. No upload or publish commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent
DEFAULT_WORK = ROOT.parent.parent/'work' if ROOT.parent.name=='outputs' else ROOT/'.work'
WORK = Path(os.environ.get('PS2_PORT_LAB_WORK', str(DEFAULT_WORK))).resolve()
SOURCE = WORK / 'dependencies/PS2Recomp'
BUILD = WORK / 'build/ps2recomp'
ENV = WORK / 'toolchain'
LOCK = json.loads((ROOT / 'toolchain.lock.json').read_text())
NETWORK_APIS = re.compile(r'\b(?:socket|connect|sendto|sendmsg|curl_\w*|WinHttp\w*|InternetOpen\w*|popen|system)\s*\(|#include[^\n]*(?:curl|socket|http|asio|winsock)', re.I)
SOURCE_FILES = ('pipeline.py', 'native.py', 'scenarios.py', 'README.md', 'CONTROLS.md', 'PRIVACY.md',
                'STATUS.md', 'RUNTIME_COMPARISON.md', '.gitignore', 'toolchain.lock.json',
                'profiles/example-game.json', 'cmake/AttachGame.cmake', 'cmake/LocalLauncher.m',
                'patches/runtime-diagnostics.patch', 'tests/test_pipeline.py',
                'tests/test_native.py', 'tests/test_scenarios.py', 'tests/runtime_timer.cpp',
                'vectors.py', 'tools/vu1_recomp.cpp', 'tools/PS2Recomp-LICENSE', 'tests/test_vectors.py',
                'tests/host_pad.cpp', 'tests/host_presentation.cpp', 'rawgs.py', 'tests/test_rawgs.py',
                'replay.py', 'tests/test_replay.py', 'memory.py', 'tests/test_memory.py',
                'compiler.py', 'tests/test_compiler.py', 'COMPILER.md',
                'dependencies.py', 'tests/test_dependencies.py',
                'toolchain.py', 'tests/test_toolchain.py',
                'adapters.py', 'tests/test_adapters.py',
                'textures.py', 'tests/test_textures.py', 'TEXTURES.md',
                'tim2.py', 'tests/test_tim2.py', 'local_sr.py', 'tests/test_local_sr.py',
                'sr_batch.py', 'tests/test_sr_batch.py', 'tools/local_text_ocr.m',
                'ate.py', 'tests/test_ate.py', 'next_game.py', 'tests/test_next_game.py',
                'release.py', 'cmake/PlayableLauncher.m', 'tools/make_app_icon.m',
                'NEXT-GAME.md', 'LESSONS.md', 'knowledge.py', 'knowledge.json',
                'tests/test_knowledge.py', 'ENGINE.md', 'enhancements.py', 'enhancements.json',
                'tests/test_enhancements.py', 'target_cli.py', 'tests/test_targets.py',
                'CONTRIBUTING.md', 'LICENSE', 'UPSTREAM-NOTICES.md',
                '.github/workflows/both-targets.yml', '.github/PULL_REQUEST_TEMPLATE.md',
                'source_freeze.py', 'tests/test_source_freeze.py', 'requirements.txt', 'platform_adapter.py', 'tests/test_platform_adapter.py', 'PLATFORMS.md', 'run.py', 'tests/test_entrypoint.py', 'tests/test_engine_fixes.py',
                'cadence.py', 'tests/test_cadence.py', 'NEW-REPOSITORIES-2026-10-17.md', 'CADENCE.md',
                'platforms/macos/adapter.json', 'platforms/macos/macos_adapter.py')


def package_source(root=ROOT):
    """Explicit public-source whitelist; never recurse into game/work directories."""
    root = Path(root).resolve()
    target = root.parent / 'PS2-Port-Lab.zip'
    temp = target.with_suffix('.zip.tmp')
    for name in SOURCE_FILES:
        p = root / name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
            raise ValueError(f'Missing or unsafe source package member: {name}')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            archive.write(root / name, 'ps2-port-lab/' + name)
    with zipfile.ZipFile(temp) as archive:
        if archive.namelist() != ['ps2-port-lab/' + n for n in SOURCE_FILES] or archive.testzip():
            raise RuntimeError('Source archive validation failed')
    temp.replace(target)
    print(f'Validated {len(SOURCE_FILES)} public-source files: {target}')
    return target


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def safe_relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or '..' in path.parts or '\\' in value:
        raise ValueError(f'Unsafe relative path: {value!r}')
    return path


PRIVATE_ROOT = ROOT / '.private'
PROFILE_COMMANDS_WITHOUT_PROFILE = {'doctor', 'bootstrap', 'bootstrap-runtime', 'source-package',
                                    'knowledge-add', 'package-accepted-app', 'new-profile'}


def private_directory(profile, root=ROOT):
    """Resolve a profile's private game storage without silently leaving the checkout.

    Default: <root>/.private/games/<id>. A relative private_dir must stay under
    <root>/.private. An existing project elsewhere needs an absolute private_dir
    plus "private_dir_absolute_opt_in": true; it may not be inside the reusable
    source tree (outside .private) or contain the source tree.
    """
    root = Path(root).resolve()
    private_root = (root / '.private').resolve()
    value = profile.get('private_dir')
    if value is None:
        resolved = (private_root / 'games' / profile['id']).resolve()
    elif not isinstance(value, str) or not value:
        raise ValueError('Profile private_dir must be a nonempty string')
    elif Path(value).is_absolute():
        if profile.get('private_dir_absolute_opt_in') is not True:
            raise ValueError('An absolute private_dir requires "private_dir_absolute_opt_in": true in the profile')
        resolved = Path(value).resolve()
    else:
        resolved = (root / safe_relative(value)).resolve()
        if not resolved.is_relative_to(private_root):
            raise ValueError('A relative private_dir must stay under .private/; for an existing project elsewhere '
                             'use an absolute path with "private_dir_absolute_opt_in": true')
    if value is None or not Path(value).is_absolute():
        # Default/relative storage is only safe while it really stays inside this
        # checkout. A symlinked .private (or a symlink below it) that resolves
        # elsewhere needs the explicit absolute private_dir opt-in instead.
        if not resolved.is_relative_to(root):
            raise ValueError('.private storage resolves outside the source tree (symlink?); use an absolute '
                             'private_dir with "private_dir_absolute_opt_in": true for storage elsewhere')
        if private_root != root / '.private':
            raise ValueError('.private storage root must not redirect into a public source directory')
    if resolved == Path('/') or root.is_relative_to(resolved):
        raise ValueError('Private game directory cannot contain the source tree')
    if resolved.is_relative_to(root) and not resolved.is_relative_to(root / '.private'):
        raise ValueError('Private game directory inside the source tree must be under .private/')
    return resolved


def load_profile(path):
    if path is None:
        raise ValueError('This command needs an explicit per-game --profile (see profiles/example-game.json)')
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f'Profile not found: {path}; create one with new-profile or next-game --title/--source')
    profile = json.loads(path.read_text())
    if not isinstance(profile, dict):
        raise ValueError('Profile must be a JSON object')
    if not isinstance(profile.get('id'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', profile['id']):
        raise ValueError('Profile id must contain lowercase letters, digits, hyphens or underscores')
    return profile, private_directory(profile)


def boot_executable(root, profile):
    if profile.get('expected_executable'):
        rel = safe_relative(profile['expected_executable'])
    else:
        text = (root / 'SYSTEM.CNF').read_text(encoding='ascii', errors='replace')
        m = re.search(r'^\s*BOOT2\s*=\s*cdrom\d?:[\\/]([^;\r\n]+);\d+', text, re.M | re.I)
        if not m:
            raise ValueError('Cannot identify BOOT2; set expected_executable in the game profile')
        rel = safe_relative(m[1].replace('\\', '/'))
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('Executable escapes disc directory')
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def elf_info(path):
    data = Path(path).read_bytes()
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected a 32-bit little-endian ELF v1')
    h = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    if h[1] != 8 or h[0] != 2:
        raise ValueError('Expected an executable MIPS ELF')

    def span(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('ELF table or section exceeds file bounds')
        return data[offset:offset + size]

    if h[11] and h[10] < 40:
        raise ValueError('Invalid ELF section entry size')
    span(h[5], h[10] * h[11])
    table = [struct.unpack_from('<IIIIIIIIII', data, h[5] + i * h[10]) for i in range(h[11])]
    if table and h[12] >= len(table):
        raise ValueError('Invalid section-name table index')
    names = span(table[h[12]][4], table[h[12]][5]) if table else b''

    def cstring(blob, offset):
        if offset >= len(blob):
            raise ValueError('ELF string index exceeds table')
        return blob[offset:].split(b'\0', 1)[0].decode('utf-8', errors='replace')

    sections = []
    functions = []
    for s in table:
        name = cstring(names, s[0])
        sections.append({'name': name, 'type': s[1], 'address': hex(s[3]), 'offset': s[4], 'bytes': s[5]})
        if s[1] == 8:  # SHT_NOBITS is not backed by file bytes.
            continue
        span(s[4], s[5])
        if s[1] != 2:
            continue
        if s[6] >= len(table) or s[9] < 16 or s[5] % s[9]:
            raise ValueError('Invalid symbol table')
        strings = span(table[s[6]][4], table[s[6]][5])
        for off in range(s[4], s[4] + s[5], s[9]):
            nameoff, value, size, info, other, index = struct.unpack_from('<IIIBBH', data, off)
            if info & 15 == 2 and index != 0:
                functions.append({'name': cstring(strings, nameoff), 'address': hex(value), 'bytes': size, 'section': index})
    clues = sorted(set(m.group().decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{8,}', data)
                       if re.search(rb'gcc2_compiled|Metrowerks|CodeWarrior|SN Systems|ProDG|/sce/ee/gcc', m.group(), re.I)))
    return {'entry': hex(h[3]), 'sha256': digest(path), 'bytes': len(data), 'sections': sections,
            'defined_function_count': len(functions), 'functions': functions,
            'compiler_evidence': clues, 'exact_compiler_version': None,
            'architecture': {'machine': 'mips', 'elf_class': 32, 'byte_order': 'little',
                'elf_flags': hex(h[6]),
                'r5900_machine_flag': (h[6] & 0x00ff0000) == 0x00920000,
                'ghidra_language_candidate': 'r5900:LE:32:default' if (h[6] & 0x00ff0000) == 0x00920000 else None,
                'scope': 'ELF header evidence only; not instruction validation or automatic REA activation. '
                         'Unmarked MIPS executables require independent ISA/ABI confirmation.'}}


def import_disc(source, profile, private):
    source = Path(source).resolve()
    dest = private / 'disc-root'
    if dest.resolve().is_relative_to(source) or source.is_relative_to(dest.resolve()):
        raise ValueError('Source and destination directories must not contain each other')
    oldpath = private / 'disc-manifest.json'
    old = json.loads(oldpath.read_text()) if oldpath.exists() else {}
    source_elf = boot_executable(source, profile)
    source_sha = digest(source_elf)
    if profile.get('expected_sha256') and source_sha != profile['expected_sha256']:
        raise ValueError('Source executable differs from profile; use the correct disc/profile')
    identity = {'executable': str(source_elf.relative_to(source)), 'sha256': source_sha}
    if old.get('identity') and old['identity'] != identity:
        raise ValueError('Source disc identity changed; create a separate profile')
    prior_elf = dest / identity['executable']
    if prior_elf.exists() and prior_elf.stat().st_size > 0 and digest(prior_elf) != source_sha:
        raise ValueError('Existing executable belongs to a different input; create a separate profile')
    known = {r['path']: r for r in old.get('files', []) if r['status'] == 'copied'}
    paths = sorted(source.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Disc input must not contain symlinks')
    files = [p for p in paths if p.is_file()]
    if not files:
        raise ValueError('No files found; is the disc mounted?')
    records = []
    for i, p in enumerate(files, 1):
        rel = str(p.relative_to(source))
        target = dest / safe_relative(rel)
        previous = known.get(rel)
        size = p.stat().st_size
        if previous and previous['bytes'] == size and target.exists() and digest(target) == previous['sha256']:
            records.append(previous)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.partial')
            h = hashlib.sha256()
            try:
                with p.open('rb') as r, temp.open('wb') as w:
                    while block := r.read(1024 * 1024):
                        w.write(block)
                        h.update(block)
                if temp.stat().st_size != size or digest(temp) != h.hexdigest():
                    raise OSError('Copy size or checksum mismatch')
                temp.replace(target)
            except OSError as e:
                save(oldpath, {'source': str(source), 'identity': identity, 'complete': False, 'files': records,
                               'error': {'path': rel, 'message': str(e)}})
                raise
            records.append({'path': rel, 'bytes': size, 'sha256': h.hexdigest(), 'status': 'copied'})
        save(oldpath, {'source': str(source), 'identity': identity, 'complete': len(records) == len(files), 'expected_file_count': len(files), 'files': records})
        print(f'[{i}/{len(files)}] {rel}', flush=True)
    print(f'Imported {len(files)} files; local-only directory copy complete')


def verify(profile, private):
    manifest = json.loads((private / 'disc-manifest.json').read_text())
    root = private / 'disc-root'
    errors = []
    if not manifest.get('files'):
        raise ValueError('Empty manifest')
    for r in manifest['files']:
        path = root / safe_relative(r['path'])
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError('Manifest path escapes disc directory')
        if r['status'] != 'copied' or not path.exists() or path.stat().st_size != r['bytes'] or digest(path) != r.get('sha256'):
            errors.append(r['path'])
    if manifest.get('complete') is False:
        errors.append('Manifest records an incomplete import')
    if manifest.get('expected_file_count', len(manifest['files'])) != len(manifest['files']):
        errors.append('Manifest file count mismatch')
    if errors:
        raise ValueError(f'Verification failed: {errors[:10]}')
    elf = boot_executable(root, profile)
    if profile.get('expected_sha256') and digest(elf) != profile['expected_sha256']:
        raise ValueError('Executable differs from profile fingerprint')
    save(private / 'verification.json', {'verified_at': time.time(), 'file_count': len(manifest['files']),
         'bytes': sum(r['bytes'] for r in manifest['files']), 'executable_sha256': digest(elf)})
    print(f'Verified all {len(manifest["files"])} files locally')


def inspect(profile, private):
    elf = boot_executable(private / 'disc-root', profile)
    info = elf_info(elf)
    save(private / 'analysis/elf.json', info)
    print(f'{elf.name}: {info["defined_function_count"]} defined function symbols; entry {info["entry"]}')
    print('Compiler strings are evidence only; exact compiler version remains unverified')
    return elf


def run(command, log, offline=False, cwd=None):
    command = [str(c) for c in command]
    policy_note = ''
    if offline:
        permitted = {str(BUILD / 'ps2xAnalyzer/ps2_analyzer'), str(BUILD / 'ps2xRecomp/ps2_recomp')}
        if command[0] not in permitted:
            raise RuntimeError('Unrecognized tool for private game processing')
        revision = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != LOCK['ps2recomp']['revision']:
            raise RuntimeError('Tool source revision differs from lock')
        for directory in [SOURCE / 'ps2xAnalyzer/src', SOURCE / 'ps2xRecomp/src']:
            for source in directory.rglob('*'):
                if source.suffix in {'.cpp', '.c', '.h', '.hpp'} and NETWORK_APIS.search(source.read_text(errors='replace')):
                    raise RuntimeError(f'Source requires a network/process API review: {source}')
        # Nested sandboxing can be rejected by the host sandbox. Be explicit:
        # source review is a precaution, not equivalent to OS network isolation.
        sandbox = Path('/usr/bin/sandbox-exec')
        if sys.platform == 'darwin' and sandbox.exists():
            prefix = [str(sandbox), '-p', '(version 1)(allow default)(deny network*)']
            probe = subprocess.run([*prefix, '/usr/bin/true'], capture_output=True)
            if probe.returncode == 0:
                command = [*prefix, *command]
                policy_note = 'OS network-deny sandbox enabled.\n'
            else:
                policy_note = 'Nested OS sandbox unavailable. Running reviewed local file tools; OS network isolation is not enforced by this wrapper.\n'
        else:
            policy_note = 'Running reviewed local file tools; OS network isolation is not available on this platform.\n'
        print(policy_note.strip(), flush=True)
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as f:
        f.write(policy_note)
        f.flush()
        result = subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
    if result.returncode:
        raise RuntimeError(f'Tool returned {result.returncode}; see local log {log}')


def bootstrap():
    # Only public tool identifiers enter these network-enabled commands.
    WORK.mkdir(parents=True, exist_ok=True)
    if not ENV.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ENV)], check=True)
    bindir = ENV / ('Scripts' if os.name == 'nt' else 'bin')
    packages = [f'{k}=={v}' for k, v in LOCK['python_build_tools'].items()]
    run([bindir / 'python', '-m', 'pip', 'install', '--no-cache-dir', *packages], WORK / 'build-logs/python-tools.log')
    ref = LOCK['ps2recomp']
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', ref['url'], str(SOURCE)], check=True)
    current = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if current != ref['revision']:
        if subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain'], text=True).strip():
            raise RuntimeError('Tool source has local changes; refusing to change its revision')
        subprocess.run(['git', '-C', str(SOURCE), 'fetch', 'origin', ref['revision']], check=True)
        subprocess.run(['git', '-C', str(SOURCE), 'checkout', '--detach', ref['revision']], check=True)
    from native import apply_runtime_patch
    apply_runtime_patch()
    run([bindir / 'cmake', '-S', SOURCE, '-B', BUILD, '-G', 'Ninja',
         f'-DCMAKE_MAKE_PROGRAM={bindir / "ninja"}', '-DCMAKE_BUILD_TYPE=Release',
         '-DPS2X_BUILD_RUNTIME=OFF', '-DPS2X_BUILD_TEST=OFF', '-DPS2X_BUILD_STUDIO=OFF'], WORK / 'build-logs/configure.log')
    for name, expected in LOCK.get('resolved_dependencies', {}).items():
        source = BUILD / '_deps' / f'{name}-src'
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != expected:
            raise RuntimeError(f'Dependency {name} differs from the locked revision')
    run([bindir / 'cmake', '--build', BUILD, '--target', 'ps2_analyzer', 'ps2_recomp', '-j', '4'], WORK / 'build-logs/build.log')
    print('Pinned analyzer and recompiler built; no game data used during setup')


def analyze(profile, private):
    elf = inspect(profile, private)
    config = private / 'analysis/recomp.toml'
    (private / 'analysis/analyzer-stage.json').unlink(missing_ok=True)
    (private / 'analysis/translation-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-syntax-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-build-stage.json').unlink(missing_ok=True)
    (private / 'analysis/boot-probe-stage.json').unlink(missing_ok=True)
    analyzer = BUILD / 'ps2xAnalyzer/ps2_analyzer'
    run([analyzer, elf, config], private / 'logs/analyze.log', offline=True, cwd=private)
    # Rewrite only path/output/diagnostic settings. Preserve analyzer decisions.
    text = config.read_text()
    for key, value in {'input': json.dumps(str(elf)), 'output': json.dumps(str(private / 'generated')),
                       'single_file_output': 'false', 'low_memory_mode': 'true', 'output_worker_threads': '1'}.items():
        if re.search(rf'^\s*{key}\s*=', text, re.M):
            text = re.sub(rf'^\s*{key}\s*=.*$', lambda m: f'{key} = {value}', text, flags=re.M)
        else:
            text = text.replace('[general]', f'[general]\n{key} = {value}', 1)
    config.write_text(text)
    save(private / 'analysis/analyzer-stage.json', {'success': True, 'config': str(config), 'tool_revision': LOCK['ps2recomp']['revision']})
    print('Analyzer finished; configuration saved locally')


CPP_SUFFIXES = {'.cpp', '.cc', '.cxx', '.c++'}


def generated_sources(directory):
    """The one generated-C++ inventory shared by translation, knowledge, syntax checks and builds.

    Every consumer (including cmake/AttachGame.cmake) reads top-level *.cpp only, so nested
    or differently suffixed C++ fails closed instead of being silently skipped.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError('No generated source directory; run translate first')
    top, unsupported = [], []
    for path in directory.rglob('*'):
        if path.suffix.lower() not in CPP_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError(f'Generated C++ must not be a symlink: {path.name}')
        if path.parent == directory and path.suffix == '.cpp':
            top.append(path)
        else:
            unsupported.append(str(path.relative_to(directory)))
    if unsupported:
        raise ValueError(f'Nested or non-.cpp generated C++ is not supported by all consumers: {sorted(unsupported)[:5]}')
    return sorted(top)


SUMMARY = re.compile(r'Functions processed: (\d+), recompiled: (\d+), stubs: (\d+), skipped: (\d+), decode failures: (\d+)')
DIAGNOSTICS = re.compile(r'Warnings: (\d+), errors: (\d+)')


def translation_acceptance(report):
    """Parse the recompiler summary. Missing/unrecognized summaries and reported errors fail closed.

    Stubs and skipped functions are reported separately: they are not implementations.
    """
    counts, blockers = {}, []
    summary, diagnostics = SUMMARY.search(report), DIAGNOSTICS.search(report)
    if summary:
        counts.update(zip(['processed', 'recompiled', 'stubs', 'skipped', 'decode_failures'], map(int, summary.groups())))
    else:
        blockers.append('recompiler function summary missing or unrecognized')
    if diagnostics:
        counts.update(warnings=int(diagnostics[1]), errors=int(diagnostics[2]))
    else:
        blockers.append('recompiler warning/error summary missing or unrecognized')
    if counts.get('decode_failures'):
        blockers.append(f"{counts['decode_failures']} instruction decode failures")
    if counts.get('errors'):
        blockers.append(f"{counts['errors']} recompiler errors")
    return dict(counts=counts, accepted=not blockers, blockers=blockers,
                stub_functions=counts.get('stubs'), skipped_functions=counts.get('skipped'),
                stubs_are_implementations=False)


def require_accepted_translation(private):
    path = Path(private) / 'analysis/translation-stage.json'
    if not path.is_file():
        raise ValueError('Run translate first')
    stage = json.loads(path.read_text())
    if stage.get('accepted') is not True:
        raise ValueError('Translation was not accepted: ' + '; '.join(stage.get('blockers') or ['legacy record without acceptance; retranslate']))
    return stage


def correct_generated_sqrt(directory):
    """Compatibility entry point backed by the executable knowledge bank."""
    from knowledge import apply_translation
    return apply_translation(directory, only={'ee-sqrt-ft'})['totals']


def translate(profile, private):
    config = private / 'analysis/recomp.toml'
    if not config.exists():
        raise ValueError('Run analyze first')
    (private / 'analysis/translation-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-syntax-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-build-stage.json').unlink(missing_ok=True)
    (private / 'analysis/boot-probe-stage.json').unlink(missing_ok=True)
    # Retain old output for debugging, but do not count stale source as new output.
    output = private / 'generated'
    if output.exists():
        archive = private / 'previous-translations' / str(time.time_ns())
        archive.parent.mkdir(parents=True, exist_ok=True)
        output.rename(archive)
    run([BUILD / 'ps2xRecomp/ps2_recomp', config], private / 'logs/translate.log', offline=True, cwd=private)
    from knowledge import apply_translation
    learned = apply_translation(private/'generated', identity=digest(boot_executable(private/'disc-root',profile)))
    corrections = learned['totals']
    save(private/'analysis/knowledge-translation.json', learned)
    save(private / 'analysis/sqrt-translation-corrections.json', corrections)
    files = generated_sources(private / 'generated')
    if not files:
        raise RuntimeError('Tool exited successfully but emitted no C++ files')
    acceptance = translation_acceptance((private / 'logs/translate.log').read_text(errors='replace'))
    save(private / 'analysis/translation-stage.json', {'success': acceptance['accepted'], **acceptance,
         'cpp_files': len(files), 'cpp_bytes': sum(p.stat().st_size for p in files),
         'tool_revision': LOCK['ps2recomp']['revision'],
         'native_build_verified': False, 'runtime_verified': False,
         'knowledge_bank_revision': learned['bank_revision'], 'knowledge_bank_sha256': learned['bank_sha256']})
    counts = acceptance['counts']
    print(f'Translation emitted {len(files)} C++ files; {counts.get("stubs", "unknown")} stub and '
          f'{counts.get("skipped", "unknown")} skipped functions are not implementations.')
    if not acceptance['accepted']:
        raise RuntimeError('Translation not accepted; downstream native stages are blocked: ' + '; '.join(acceptance['blockers']))
    print('Native build and gameplay remain unverified.')


def check_generated(profile, private):
    """Check representative generated source with the native compiler, not a game build."""
    directory = private / 'generated'
    require_accepted_translation(private)
    files = generated_sources(directory)
    if not files:
        raise ValueError('Run translate first')
    # Spread a general sample across the inventory and include largest functions.
    sample = {files[0], files[-1], *files[::max(1, len(files) // 8)]}
    sample.update(sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:2])
    compiler = shutil.which('clang++')
    if not compiler:
        raise RuntimeError('Native clang++ is missing')
    flags = [compiler, '-std=c++20', '-fsyntax-only', '-ferror-limit=8',
             '-I', str(SOURCE / 'ps2xRuntime/include'), '-I', str(SOURCE / 'ps2xRuntime/src/lib/Kernel'),
             '-I', str(SOURCE / 'ps2xIOP/include'), '-I', str(directory)]
    if platform.machine() == 'arm64':
        flags += ['-DUSE_SSE2NEON', '-I', str(BUILD / '_deps/sse2neon-src')]
    records = []
    for p in sorted(sample):
        log = private / 'logs/native-syntax' / (p.name + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w') as f:
            result = subprocess.run([*flags, str(p)], stdout=f, stderr=subprocess.STDOUT)
        records.append({'file': p.name, 'returncode': result.returncode, 'log': str(log)})
        print(f'{"PASS" if result.returncode == 0 else "FAIL"} {p.name}', flush=True)
    save(private / 'analysis/native-syntax-stage.json', {'sample_size': len(records), 'files': records,
         'success': all(r['returncode'] == 0 for r in records), 'full_native_build_verified': False})
    if any(r['returncode'] for r in records):
        raise RuntimeError('Representative generated source failed native syntax checks; inspect local logs')
    print(f'{len(records)} representative files passed; full build and runtime remain unverified')


def status(profile, private):
    print(f'{profile["title"]}: {private}')
    for stage, file in [('Verification', 'verification.json'), ('Inspection', 'analysis/elf.json'),
                        ('Analyzer', 'analysis/analyzer-stage.json'), ('Translation', 'analysis/translation-stage.json'),
                        ('Native syntax sample', 'analysis/native-syntax-stage.json'),
                        ('Native build', 'analysis/native-build-stage.json'), ('First boot probe', 'analysis/boot-probe-stage.json')]:
        print(stage + ': ' + ('recorded (see private artifact)' if (private / file).exists() else 'not recorded'))
    print('Runtime: ' + profile.get('runtime_status', 'not_integrated'))


def main(target=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Explicit per-game profile JSON (required for game processing)')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ['doctor', 'bootstrap', 'bootstrap-runtime', 'build-native', 'test-runtime', 'boot-probe', 'diagnose', 'package-local', 'verify', 'inspect', 'analyze', 'translate', 'check-generated', 'prepare', 'status']:
        sub.add_parser(cmd)
    sub.add_parser('import').add_argument('source')
    sub.add_parser('source-package')
    sub.add_parser('knowledge-plan', help='Detect reusable contracts and run configured knowledge gates')
    sub.add_parser('enhancement-plan', help='Schedule every graphics/game/runtime stage; missing evidence cannot pass')
    freeze = sub.add_parser('freeze-source', help='Rebuild and freeze a unit using actual compiler-discovered dependencies')
    for option in ['compile-db','unit','source','reference','name']:
        freeze.add_argument('--'+option, required=True)
    bank = sub.add_parser('knowledge-add', help='Append a data-only rule after regression validation')
    bank.add_argument('--bank', default=str(ROOT/'knowledge.json'))
    bank.add_argument('--candidate', required=True)
    next_game = sub.add_parser('next-game', help='Run offline next-title intake/translation and report integration gaps')
    next_game.add_argument('--source', help='Mounted disc directory; omit after import')
    next_game.add_argument('--title', help='Create a missing profile, discover BOOT2 and pin the executable revision')
    next_game.add_argument('--name', required=True)
    next_game.add_argument('--through', choices=('intake', 'translate'), default='intake')
    next_game.add_argument('--execute', action='store_true', help='Execute fixed local stages; default only plans')
    next_game.add_argument('--resume', action='store_true')
    release = sub.add_parser('package-accepted-app', help='Wrap a preserved accepted GPU build in a normal local Mac app')
    for option in ['source', 'destination', 'title', 'version', 'bundle-id', 'icon', 'log']:
        release.add_argument('--'+option, required=True)
    memory = sub.add_parser('memory-audit', help='Reject runtime pools overlapping declared game memory')
    memory.add_argument('--runtime-header', required=True, help='Local runtime Helpers/State.h used for this build')
    memory.add_argument('--layout', required=True, help='Private JSON layout path, relative to game storage')
    memory.add_argument('--name', required=True, help='Unique local audit name')
    for command in ['compiler-plan', 'compiler-stage']:
        recipe = sub.add_parser(command, help='Validate a pinned local compiler recipe; optionally stage isolated sources')
        recipe.add_argument('--recipe', required=True, help='Recipe JSON inside private game storage')
        recipe.add_argument('--name', required=True, help='Unique local compiler run name')
    sub.add_parser('compiler-toolchain', help='Pin the selected local macOS compiler/linker/SDK').add_argument('--name', required=True)
    adapters = sub.add_parser('stage-adapters', help='Stage guarded title-specific module copies; never alter the disc')
    adapters.add_argument('--manifest',required=True)
    adapters.add_argument('--name',required=True)
    textures = sub.add_parser('enhance-textures', help='Process private decoded textures locally with a verified cache')
    textures.add_argument('--manifest', required=True)
    textures.add_argument('--name', required=True)
    sub.add_parser('compile-vu').add_argument('capture',help='Captured vector-code directory inside private game storage')
    replay = sub.add_parser('replay-raw-gs', help='Replay a private capture through a pinned local backend')
    replay.add_argument('capture', help='Capture path relative to private game storage')
    replay.add_argument('--backend', required=True, help='Private pinned backend JSON path')
    replay.add_argument('--name', required=True, help='Unique local run name')
    replay.add_argument('--batch', choices=('on', 'off'), default='on')
    replay.add_argument('--timeout', type=int, default=300)
    cadence = sub.add_parser('analyze-cadence', help='Separate host presentation, emulated speed and fresh-image cadence')
    cadence.add_argument('capture', help='Telemetry CSV inside private game storage')
    cadence.add_argument('--name', required=True)
    cadence.add_argument('--expected-field-hz', type=float, default=60000/1001)
    scenario = sub.add_parser('scenario')
    scenario.add_argument('definition', help='JSON definition inside the private game directory')
    scenario.add_argument('--name', required=True, help='Unique local run name')
    scenario.add_argument('--renderer-worker', choices=('off','on'), help='Recorded renderer treatment; scene definition and inputs stay identical')
    scenario.add_argument('--graphics-history', choices=('off','on'), help='Recorded heavy graphics-trace treatment; scene inputs stay identical')
    comparison = sub.add_parser('compare-scenarios')
    comparison.add_argument('baseline', help='Baseline local run name')
    comparison.add_argument('candidate', help='Candidate local run name')
    comparison.add_argument('--name', required=True, help='Unique local comparison name')
    new = sub.add_parser('new-profile', help='Create a per-game profile under .private/profiles (never inside public profiles/)')
    new.add_argument('id')
    new.add_argument('--title', required=True)
    new.add_argument('--source', help='Mounted disc directory; pins BOOT2 executable and SHA-256')
    new.add_argument('--output', help='Profile path (default .private/profiles/<id>.json); filename must be <id>.json')
    args = parser.parse_args()
    # A next-game parent passes its resolved tool/work root. A mismatch means this
    # child would use different tools than the parent's resume contract pinned.
    expected = os.environ.get('PS2_PORT_LAB_EXPECTED_WORK')
    if expected and Path(expected).resolve() != WORK:
        raise RuntimeError(f'Work directory mismatch: parent pinned {expected}, child resolved {WORK}')
    if expected:
        print(f'[pipeline] work={WORK}', flush=True)
    if target is not None:
        from target_cli import check_target
        try: check_target(target, args.command)
        except ValueError as error: parser.error(str(error))
    if args.command == 'source-package':
        package_source()
        return
    if args.command == 'knowledge-add':
        from knowledge import add_rule
        print(json.dumps(add_rule(args.bank, args.candidate), indent=2))
        return
    if args.command == 'package-accepted-app':
        from release import package
        report = package(args.source, args.destination, args.title, args.version, args.bundle_id, args.icon, args.log)
        print(json.dumps(report, indent=2))
        return
    if args.command == 'next-game':
        from next_game import run, create_profile
        if args.profile is None:
            raise ValueError('next-game needs an explicit per-game --profile path (it is created when missing)')
        if not Path(args.profile).exists():
            if not args.title or not args.source:
                raise ValueError('New profile requires --title and --source')
            if not args.execute:
                raise ValueError('Create a profile with new-profile first, or use --execute with --title/--source')
            create_profile(args.profile, args.title, args.source)
        elif args.title:
            existing, _ = load_profile(args.profile)
            if existing.get('title') != args.title:
                raise ValueError(f'Profile {args.profile} belongs to {existing.get("title")!r}; use a separate profile for another title')
        report = run(args.profile, args.name, args.source, args.through, args.execute, args.resume)
        print(json.dumps({'status':report['status'], 'playable_certified':False,
                          'remaining_gates':report['remaining_gates']}, indent=2))
        if report['status'] == 'blocked': sys.exit(2)
        return
    if args.command == 'doctor':
        print(platform.platform())
        print('Python:', sys.version.split()[0])
        for name in ['git', 'clang++', 'sandbox-exec']:
            print(name + ':', shutil.which(name) or 'missing')
        print('Tool work:', WORK)
        print('PS2Recomp built:', (BUILD / 'ps2xRecomp/ps2_recomp').exists())
        return
    if args.command == 'bootstrap':
        bootstrap()
        return
    if args.command == 'bootstrap-runtime':
        from native import bootstrap_runtime
        bootstrap_runtime()
        return
    if args.command == 'new-profile':
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.id):
            raise ValueError('Invalid profile id')
        path = Path(args.output).resolve() if args.output else PRIVATE_ROOT / f'profiles/{args.id}.json'
        if path.stem != args.id or path.suffix != '.json':
            raise ValueError('Profile filename must be <id>.json')
        if path.exists():
            raise ValueError('Profile already exists')
        if args.source:
            from next_game import create_profile
            create_profile(path, args.title, args.source)
        else:
            save(path, {'id': args.id, 'title': args.title, 'runtime_status': 'not_integrated'})
        print(path)
        print(f'Use it explicitly: python3 run.py --profile "{path}" <command>')
        return
    if args.command in PROFILE_COMMANDS_WITHOUT_PROFILE:
        raise RuntimeError('Unhandled profile-free command: ' + args.command)
    profile, private = load_profile(args.profile)
    private.mkdir(parents=True, exist_ok=True)
    if args.command == 'freeze-source':
        from source_freeze import freeze
        report=freeze(profile,private,args.compile_db,args.unit,args.source,args.reference,args.name)
        print(json.dumps({'status':report['status'],'text_matches':report.get('text_matches'),
                          'probe_matches_reference':report.get('probe_matches_reference'),
                          'whole_game_clean_build':False},indent=2))
        if report['status']!='passed': sys.exit(2)
    elif args.command == 'enhancement-plan':
        from enhancements import schedule
        report=schedule(profile,private)
        print(json.dumps({'qualified':report['completed'],'total':report['total'],'policy':report['policy']},indent=2))
    elif args.command == 'knowledge-plan':
        from knowledge import plan
        report = plan(profile, private)
        print(json.dumps(report, indent=2))
        if report.get('memory') and not report['memory']['accepted']: sys.exit(2)
    elif args.command == 'import':
        import_disc(args.source, profile, private)
    elif args.command == 'enhance-textures':
        from textures import enhance
        report = enhance(profile, private, args.manifest, args.name)
        print(json.dumps({'status': report['status'], 'textures': len(report['textures']),
                          'runtime_integration': report['runtime_integration'],
                          'report': str(private/'texture-runs'/args.name/'report.json')}))
    elif args.command == 'stage-adapters':
        from adapters import stage_adapters
        report=stage_adapters(profile,private,args.manifest,args.name)
        print(json.dumps({'status':report['status'],'adapters':len(report['adapters']),
                         'report':str(private/'adapter-runs'/args.name/'report.json')}))
    elif args.command == 'compiler-toolchain':
        from toolchain import record_host
        print(record_host(private, args.name))
    elif args.command in {'compiler-plan', 'compiler-stage'}:
        from compiler import run_recipe
        report = run_recipe(profile, private, args.recipe, args.name, stage=args.command=='compiler-stage')
        print(json.dumps({'status':report['status'], 'clean_build_ready':report['clean_build_ready'],
            'blockers':report['blockers'], 'report':str(private/'compiler-runs'/args.name/'report.json')}))
    elif args.command == 'memory-audit':
        from memory import run_audit
        report = run_audit(profile, private, args.runtime_header, args.layout, args.name)
        print(json.dumps({'status': report['status'], 'accepted': report['accepted'],
                          'report': str(private/'analysis/memory-audits'/args.name/'report.json')}))
        if not report['accepted']:
            sys.exit(2)
    elif args.command == 'replay-raw-gs':
        from replay import run_replay
        report = run_replay(profile, private, args.capture, args.backend, args.name, args.batch, args.timeout)
        print(json.dumps({'status': report['status'], 'matches_recorded_footer': report.get('matches_recorded_footer'),
                          'report': str(private / 'logs/replays' / args.name / 'report.json')}))
        if report['status'] != 'observed':
            sys.exit(2)
    elif args.command == 'analyze-cadence':
        from cadence import analyze_capture
        report = analyze_capture(profile, private, args.capture, args.name, args.expected_field_hz)
        print(json.dumps(report))
    elif args.command == 'compile-vu':
        from vectors import compile_vu
        compile_vu(profile,private,args.capture)
    elif args.command == 'scenario':
        from scenarios import run_scenario
        report = run_scenario(profile, private, args.definition, args.name, args.renderer_worker, args.graphics_history)
        if report['status'] != 'observed':
            sys.exit(2)
    elif args.command == 'compare-scenarios':
        from scenarios import compare_scenarios
        report=compare_scenarios(private, args.baseline, args.candidate, args.name)
        if not report['eligible_for_timing_comparison']:
            sys.exit(2)
    elif args.command == 'prepare':
        verify(profile, private)
        inspect(profile, private)
        analyze(profile, private)
        translate(profile, private)
    elif args.command == 'check-generated':
        check_generated(profile, private)
    elif args.command in {'build-native', 'test-runtime', 'boot-probe', 'package-local'}:
        import native
        getattr(native, args.command.replace('-', '_'))(profile, private)
    elif args.command == 'diagnose':
        import native
        native.boot_probe(profile, private, headless=True)
    else:
        globals()[args.command](profile, private)


def cli(target=None):
    """Shared command-line wrapper for pipeline.py, run.py targets and platform adapters."""
    try:
        main(target)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    cli()
