"""Public runtime provisioning and local per-game builds for PS2 Port Lab."""
import contextlib
from pathlib import Path
import json
import os
import subprocess
import time
import plistlib
import shutil
import pipeline as lab

PREFIX = lab.WORK / 'runtime-deps'
PUBLIC_BUILD = lab.WORK / 'build/runtime'


def runtime_environment():
    env = os.environ.copy()
    env['PATH'] = str(lab.ENV / 'bin') + os.pathsep + env.get('PATH', '')
    env['PKG_CONFIG_PATH'] = str(PREFIX / 'lib/pkgconfig')
    return env


def logged(command, log, cwd=None, timeout=None, allow_failure=False, env_patch=None):
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as f:
        try:
            environment = runtime_environment()
            environment.update(env_patch or {})
            result = subprocess.run([str(x) for x in command], cwd=cwd,
                                    env=environment, stdout=f, stderr=subprocess.STDOUT,
                                    timeout=timeout)
        except subprocess.TimeoutExpired:
            return 'timeout'
    if result.returncode and allow_failure:
        return f'exit_{result.returncode}'
    if result.returncode:
        raise RuntimeError(f'Native stage returned {result.returncode}; see local log {log}')
    return 'success'


def public_checkout(name, url, revision):
    path = lab.WORK / 'dependencies' / name
    if not path.exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', revision, url, str(path)], check=True)
    current = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    pinned = lab.LOCK.get('runtime_sources', {}).get(name, {}).get('revision')
    if pinned and current != pinned:
        raise RuntimeError(f'{name} differs from the locked revision')
    return path


def common_configure(build):
    return [lab.ENV / 'bin/cmake', '-S', lab.SOURCE, '-B', build, '-G', 'Ninja',
            f'-DCMAKE_MAKE_PROGRAM={lab.ENV / "bin/ninja"}', '-DCMAKE_BUILD_TYPE=Release',
            f'-DPKG_CONFIG_EXECUTABLE={PREFIX / "bin/pkgconf"}',
            '-DPS2X_BUILD_RECOMP=OFF', '-DPS2X_BUILD_ANALYZER=OFF', '-DPS2X_BUILD_TEST=OFF',
            '-DPS2X_BUILD_STUDIO=OFF', '-DPS2X_ENABLE_DEBUG_UI=OFF', '-DPS2X_ENABLE_RUNTIME_LOGS=OFF',
            '-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF', '-DPS2X_ENABLE_SCCACHE=OFF',
            f'-DFETCHCONTENT_SOURCE_DIR_RAYLIB={lab.WORK / "dependencies/raylib"}',
            f'-DFETCHCONTENT_SOURCE_DIR_SSE2NEON={lab.BUILD / "_deps/sse2neon-src"}']


def bootstrap_runtime():
    """Download/build public dependencies without using any game input."""
    pkgconf = public_checkout('pkgconf', 'https://github.com/pkgconf/pkgconf.git', 'pkgconf-2.5.1')
    ffmpeg = public_checkout('FFmpeg', 'https://github.com/FFmpeg/FFmpeg.git', 'n7.1.2')
    public_checkout('raylib', 'https://github.com/raysan5/raylib.git', '5.5')
    apply_runtime_patch()
    logs = lab.WORK / 'build-logs'
    logged([lab.ENV / 'bin/python', '-m', 'pip', 'install', '--no-cache-dir', 'meson==1.9.1'], logs / 'meson.log')
    pkgbuild = lab.WORK / 'build/pkgconf'
    if not (pkgbuild / 'build.ninja').exists():
        logged([lab.ENV / 'bin/meson', 'setup', pkgbuild, pkgconf, '--prefix', PREFIX, '-Dtests=disabled'], logs / 'pkgconf-configure.log')
    logged([lab.ENV / 'bin/meson', 'compile', '-C', pkgbuild], logs / 'pkgconf-build.log')
    logged([lab.ENV / 'bin/meson', 'install', '-C', pkgbuild], logs / 'pkgconf-install.log')
    ffbuild = lab.WORK / 'build/ffmpeg'
    ffbuild.mkdir(parents=True, exist_ok=True)
    if not (ffbuild / 'config.mak').exists() and not (ffbuild / 'ffbuild/config.mak').exists():
        logged([ffmpeg / 'configure', f'--prefix={PREFIX}', '--disable-programs', '--disable-doc',
                '--disable-network', '--disable-everything', '--enable-decoder=mpeg2video',
                '--enable-parser=mpegvideo', '--enable-swscale', '--enable-swresample',
                '--enable-avformat', '--enable-protocol=file', '--disable-autodetect'], logs / 'ffmpeg-configure.log', cwd=ffbuild)
    logged(['make', '-j', '4'], logs / 'ffmpeg-build.log', cwd=ffbuild)
    logged(['make', 'install'], logs / 'ffmpeg-install.log', cwd=ffbuild)
    logged(common_configure(PUBLIC_BUILD), logs / 'runtime-configure.log')
    logged([lab.ENV / 'bin/cmake', '--build', PUBLIC_BUILD, '--target', 'ps2_runtime', '-j', '4'], logs / 'runtime-build.log')
    print('Public runtime library built; no game data involved')


def build_native(profile, private):
    # The native product must consume current executable knowledge and a declared
    # memory contract; a linked runner is unsafe evidence if these are missing.
    from knowledge import load, plan, sha
    # Missing/unrecognized recompiler summaries and reported decode/errors block native builds.
    stage = lab.require_accepted_translation(private)
    lab.generated_sources(private / 'generated')  # nested/unsupported generated C++ fails closed
    bank = load()
    expected_bank = sha(json.dumps(bank,sort_keys=True).encode())
    if stage.get('knowledge_bank_sha256') != expected_bank:
        raise ValueError('Translation predates or differs from the executable knowledge bank; retranslate in an isolated run')
    contracts = plan(profile, private)
    if not contracts.get('memory') or not contracts['memory']['accepted']:
        raise ValueError('Native build needs a passing configured engine_memory ownership gate; do not guess free guest addresses')
    apply_runtime_patch()
    generated = private / 'generated'
    if not (private / 'analysis/translation-stage.json').exists():
        raise ValueError('Run translate first')
    corrections = lab.correct_generated_sqrt(generated)
    lab.save(private / 'analysis/sqrt-translation-corrections.json', corrections)
    raylib = lab.WORK / 'dependencies/raylib'
    if not raylib.exists() or not (PREFIX / 'lib/libavcodec.a').exists():
        raise ValueError('Run bootstrap-runtime before building game code')
    build = private / 'native-build'
    vu_directory,vu_record = '',None
    if profile.get('compiled_vu',False):
        from vectors import validated_sources
        vu_directory,vu_record=validated_sources(private)
    command = common_configure(build) + [
        '-DFETCHCONTENT_FULLY_DISCONNECTED=ON',
        f'-DFETCHCONTENT_SOURCE_DIR_RAYLIB={raylib}',
        f'-DCMAKE_PROJECT_INCLUDE={lab.ROOT / "cmake/AttachGame.cmake"}',
        f'-DPS2_PORT_UPSTREAM={lab.SOURCE}', f'-DPS2_PORT_GENERATED_DIR={generated}',
        f'-DPS2_PORT_TEST_SOURCE={lab.ROOT / "tests/runtime_timer.cpp"}',
        f'-DPS2_PORT_VU_COMPILER_SOURCE={lab.ROOT / "tools/vu1_recomp.cpp"}',
        f'-DPS2_PORT_VU_COMPILED_DIR={vu_directory}',
        '-DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=16']
    record = private / 'analysis/native-build-stage.json'
    record.unlink(missing_ok=True)
    with private_patches_applied(profile, private) as title_patches:
        logged(command, private / 'logs/native-configure.log')
        logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2EntryRunner', 'ps2PortVuRecompiler', '-j', '2'], private / 'logs/native-build.log')
    binary = build / 'ps2xRuntime/ps2EntryRunner'
    if not binary.exists():
        raise RuntimeError('Native build reported success without the runner binary')
    lab.save(record, {'success': True, 'binary': str(binary), 'sha256': lab.digest(binary),
                     'runtime_verified': False, 'built_at': time.time(),
                     'vu_execution_supported':True,'compiled_vu':vu_record,
                     'runtime_patches': {name: lab.digest(lab.ROOT / name) for name in RUNTIME_PATCHES},
                     'private_source_patches': {p.name: lab.digest(p) for p in title_patches}})
    print('Native runner linked locally. Gameplay remains unverified.')



def test_runtime(profile, private):
    """Run synthetic runtime behavior checks without retail game code."""
    build = private / 'native-build'
    if not (build / 'build.ninja').exists():
        raise ValueError('Run build-native to configure the local runtime first')
    logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2PortRuntimeTests', 'ps2PortVuTests', 'ps2PortHostPadTests', 'ps2PortPresentationTests', 'ps2PortIopImportTests', 'ps2PortLazyTimerTests', 'ps2PortVifUnpackTests', '-j', '2'],
           private / 'logs/runtime-test-build.log')
    for worker, name in [('0', 'runtime-test.log'), ('1', 'runtime-worker-test.log')]:
        outcome = logged([build / 'ps2PortRuntimeTests'], private / 'logs' / name, timeout=10,
                         env_patch={'PS2_PORT_GS_THREAD': worker})
        if outcome != 'success':
            raise RuntimeError('Synthetic runtime test timed out; inspect the local log')
    if logged([build / 'ps2PortPresentationTests'], private / 'logs/host-presentation-test.log', timeout=15) != 'success':
        raise RuntimeError('Synthetic host presentation checks failed; inspect the local log')
    if logged([build / 'ps2PortHostPadTests'], private / 'logs/host-pad-test.log', timeout=10) != 'success':
        raise RuntimeError('Synthetic host input checks failed; inspect the local log')
    if logged([build / 'ps2PortIopImportTests'], private / 'logs/iop-import-test.log', timeout=15) != 'success':
        raise RuntimeError('Synthetic IOP imports failed; inspect the local log')
    if logged([build / 'ps2PortVuTests'], private / 'logs/vu-test.log', timeout=60) != 'success':
        raise RuntimeError('Synthetic VU suite timed out; inspect the local log')
    # Test the carried host implementations, not merely upstream stubs.
    import sys
    for component in ('test_native_audio.py', 'test_gpu_components.py', 'test_frame_ownership.py'):
        if logged([sys.executable, '-m', 'unittest', 'discover', '-s', str(lab.ROOT/'tests'), '-p', component],
                  private/'logs'/('component-'+component+'.log'), cwd=lab.ROOT, timeout=90) != 'success':
            raise RuntimeError('Native component regression failed: '+component)
    from runtime_hotpaths import verify as verify_hotpaths
    verify_hotpaths(build, private / "logs/hotpaths")
    test_iop(private)
    gs_build = lab.WORK / 'build/gs-cache-check'
    command = [lab.ENV / 'bin/cmake', '-S', lab.SOURCE / 'ps2xTest/gs_cache',
               '-B', gs_build, '-G', 'Ninja',
               f'-DCMAKE_MAKE_PROGRAM={lab.ENV / "bin/ninja"}', '-DCMAKE_BUILD_TYPE=Release']
    if lab.platform.machine() == 'arm64':
        command.append(f'-DCMAKE_CXX_FLAGS=-DUSE_SSE2NEON -I{lab.BUILD / "_deps/sse2neon-src"}')
    logged(command, private / 'logs/graphics-test-configure.log')
    logged([lab.ENV / 'bin/cmake', '--build', gs_build, '-j', '3'], private / 'logs/graphics-test-build.log')
    if logged([lab.ENV / 'bin/ctest', '--test-dir', gs_build, '--output-on-failure'],
              private / 'logs/graphics-test.log', timeout=60) != 'success':
        raise RuntimeError('Synthetic graphics suite timed out; inspect the local log')
    lab.save(private/'analysis/runtime-tests-stage.json',dict(success=True,gameplay_verified=False,logs={str(f.relative_to(private)):lab.digest(f) for f in sorted((private/'logs').rglob('*')) if f.is_file() and ('test' in f.name or 'hotpath' in str(f))}))
    print('Synthetic runtime in synchronous/worker modes, IOP imports, host input/presentation, 43 VU and 45 texture, palette and VRAM checks passed; no retail code executed.')


# Applied in order over the pinned PS2Recomp revision. The IOP patch carries the SPU2 device,
# scheduler conformance (RPC servers honour thread priority, blocking RPC, alarms), AutoDMA and
# their CTest suites; it stacks on the diagnostics patch. Title conformance carries the generic
# kernel/IOP/CD/GS/MPEG behaviour a second title needed (see ENGINE.md "Runtime title conformance").
RUNTIME_PATCHES = ('patches/runtime-diagnostics.patch', 'patches/ps2xIOP-port-lab.patch', 'patches/scalar-fpu.patch',
                   'patches/runtime-title-conformance.patch', 'patches/scalar-fpu-compare.patch',
                   'patches/runtime-hotpaths.patch', 'patches/native-pcm.patch','patches/host-input.patch')


def private_source_patches(profile, private):
    """Title-specific source patches named by a private profile, resolved inside its private directory.

    Title patches (for example a driver workaround matched by instruction signature) stay private:
    they encode retail code and must record the emulator behaviour they compensate for."""
    patches = []
    for item in (profile or {}).get('private_source_patches', []):
        if not isinstance(item, dict) or not item.get('file') or not item.get('reason') or not item.get('sha256'):
            raise ValueError('Each private source patch needs file, sha256 and the emulator behaviour it compensates for (reason)')
        path = Path(private) / lab.safe_relative(item['file'])
        if not path.resolve().is_relative_to(Path(private).resolve()) or not path.is_file():
            raise ValueError(f'Private source patch must be a file inside the private directory: {item["file"]}')
        if lab.digest(path) != item['sha256']:
            raise ValueError(f'Private source patch changed: {item["file"]}')
        patches.append(path)
    return patches


def _git_apply(patch, *flags):
    return subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', *flags, str(patch)], capture_output=True, text=True)


def _patch_paths(patch):
    return [line[6:].strip() for line in Path(patch).read_text(errors='replace').splitlines()
            if line.startswith('+++ b/')]


def _stack_already_applied(patches):
    """True when the checkout's patched files equal HEAD plus the whole stack, applied in order.

    Computed in a throwaway index, so an up-to-date checkout is left untouched (no rebuild churn).
    Later patches edit lines earlier ones touched, so patches cannot be checked one at a time."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
        git = ['git', '-C', str(lab.SOURCE)]
        if subprocess.run([*git, 'read-tree', 'HEAD'], env=env, capture_output=True).returncode:
            return False
        for patch in patches:
            if subprocess.run([*git, 'apply', '--cached', str(patch)], env=env, capture_output=True).returncode:
                return False
        paths = sorted({path for patch in patches for path in _patch_paths(patch)})
        return subprocess.run([*git, 'diff', '--quiet', '--', *paths], env=env, capture_output=True).returncode == 0


def apply_runtime_patch():
    """Bring the shared tool checkout to pinned revision + the public patch stack (idempotent).

    If the stack is not exactly applied, peel applied patches off from the top, then apply the
    whole stack in order. Unknown edits fail explicitly."""
    patches = []
    for name in RUNTIME_PATCHES:
        patch = lab.ROOT / name
        if not patch.exists():
            raise FileNotFoundError(f'Runtime source patch missing: {patch}')
        patches.append(patch)
    record = _applied_stack_dir()
    if _stack_already_applied(patches):
        if not record.exists():
            _record_applied_stack(patches)
        return
    # Peel what was actually applied last time: a patch file edited in place (a folded fix)
    # no longer reverses from its new content, so the recorded copies are used first.
    recorded = sorted(record.glob('*.patch')) if record.exists() else []
    for patch in reversed(recorded):
        if _git_apply(patch, '--reverse', '--check').returncode == 0:
            subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', '--reverse', str(patch)], check=True)
    if record.exists():
        shutil.rmtree(record)
    for patch in reversed(patches):
        if _git_apply(patch, '--reverse', '--check').returncode == 0:
            subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', '--reverse', str(patch)], check=True)
    for patch in patches:
        check = _git_apply(patch, '--check')
        if check.returncode:
            raise RuntimeError(f'{patch.name} does not apply to the tool source; it must be the pinned revision '
                               f'with the earlier patches applied and no other edits.\n{check.stderr.strip()}')
        subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', str(patch)], check=True)
    _record_applied_stack(patches)


def _applied_stack_dir():
    """Untracked copy of the patch contents last applied to the shared checkout (inside .git)."""
    return lab.SOURCE / '.git' / 'port-lab-applied-stack'


def _record_applied_stack(patches):
    record = _applied_stack_dir()
    temp = record.with_name(record.name + '.tmp')
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    for index, patch in enumerate(patches):
        shutil.copyfile(patch, temp / f'{index:02d}-{Path(patch).name}')
    if record.exists():
        shutil.rmtree(record)
    temp.rename(record)


@contextlib.contextmanager
def private_patches_applied(profile, private):
    """Apply a title's private source patches for one build, then restore the public source."""
    applied = []
    try:
        for patch in private_source_patches(profile, private):
            check = _git_apply(patch, '--check')
            if check.returncode:
                raise RuntimeError(f'Private source patch does not apply over the public source: {patch.name}\n{check.stderr.strip()}')
            subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', str(patch)], check=True)
            applied.append(patch)
        yield applied
    finally:
        for patch in reversed(applied):
            subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', '--reverse', str(patch)], check=True)


def test_iop(private):
    """Build the patched IOP alone and run its conformance suites (CTest); no game code involved."""
    build = Path(private) / 'iop-conformance-build'
    logged([lab.ENV / 'bin/cmake', '-S', lab.SOURCE / 'ps2xIOP', '-B', build, '-G', 'Ninja',
            f'-DCMAKE_MAKE_PROGRAM={lab.ENV / "bin/ninja"}', '-DCMAKE_BUILD_TYPE=Release', '-DPS2X_IOP_BUILD_TESTS=ON'],
           Path(private) / 'logs/iop-conformance-configure.log')
    logged([lab.ENV / 'bin/cmake', '--build', build, '-j', '2'], Path(private) / 'logs/iop-conformance-build.log')
    if logged([lab.ENV / 'bin/ctest', '--test-dir', build, '--output-on-failure'],
              Path(private) / 'logs/iop-conformance.log', timeout=120) != 'success':
        raise RuntimeError('IOP conformance suite failed or timed out; inspect the local log')


def boot_probe(profile, private, headless=False):
    """Bounded first-boot observation; no claim of gameplay correctness."""
    stage = json.loads((private / 'analysis/native-build-stage.json').read_text())
    binary = Path(stage['binary'])
    if lab.digest(binary) != stage['sha256']:
        raise ValueError('Native binary differs from build record')
    # Reviewed source has no host socket/upload calls. FFmpeg is built without
    # network protocols. The host's nested sandbox limitation remains documented.
    for directory in [lab.SOURCE / 'ps2xRuntime/src', lab.SOURCE / 'ps2xIOP/src']:
        for p in directory.rglob('*'):
            if p.suffix in {'.cpp', '.c', '.h', '.hpp'} and lab.NETWORK_APIS.search(p.read_text(errors='replace')):
                raise RuntimeError(f'Runtime source requires network/process API review: {p}')
    elf = lab.boot_executable(private / 'disc-root', profile)
    name = 'headless-probe' if headless else 'boot-probe'
    log = private / f'logs/{name}.log'
    frame = private / 'logs/headless-frame.ppm'
    if headless:
        frame.unlink(missing_ok=True)
    environment = {'PS2_PORT_IO_ROOT': str((private / 'disc-root').resolve())}
    image = cd_image(private)
    if image:
        environment['PS2_PORT_CD_IMAGE'] = str(image)
    if headless:
        environment.update(PS2_PORT_HEADLESS='1', PS2_PORT_MPEG_TRACE='1',
                           PS2_PORT_FRAME=str(private / 'logs/headless-frame.ppm'),
                           PS2_PORT_GS_HISTORY=str(private / 'logs/graphics-history.jsonl'))
    outcome = logged([binary, elf], log, cwd=private / 'disc-root', timeout=30, allow_failure=True,
                     env_patch=environment)
    lab.save(private / f'analysis/{name}-stage.json', {'outcome': outcome, 'duration_limit_seconds': 30, 'cd_image': str(image) if image else None,
             'gameplay_verified': False, 'log': str(log), 'headless': headless, 'frame_capture': str(frame) if headless and frame.exists() else None})
    print(f'First boot probe: {outcome}. Inspect local diagnostics; no gameplay claim.')


def cd_image(private):
    """Verified sector image from import-image, passed to the runtime as PS2_PORT_CD_IMAGE."""
    from cdimage import verified_image
    return verified_image(private)


def stage_runtime_disc(private, destination):
    """Verified local runtime copy; preserve the original archive and existing saves."""
    source = (private / 'disc-root').resolve()
    destination = Path(destination)
    if destination.is_symlink() or destination.resolve() == source:
        raise ValueError('Runtime storage must be a separate real directory')
    manifest = json.loads((private / 'disc-manifest.json').read_text())
    records = manifest.get('files', [])
    if not records or manifest.get('complete') is False or manifest.get('expected_file_count', len(records)) != len(records):
        raise ValueError('Runtime staging requires a complete disc manifest')
    if not destination.exists():
        destination.mkdir(parents=True, mode=0o700)
    if destination.stat().st_mode & 0o777 != 0o700:
        destination.chmod(0o700)
    root = destination.resolve()
    copied = 0
    seen = set()
    for record in records:
        relative = lab.safe_relative(record['path'])
        if str(relative) in seen or record.get('status') != 'copied':
            raise ValueError('Invalid or repeated disc manifest record')
        seen.add(str(relative))
        original, target = source / relative, root / relative
        if (original.is_symlink() or not original.resolve().is_relative_to(source) or
                target.is_symlink() or not target.resolve().is_relative_to(root)):
            raise ValueError('Runtime staging path escapes its root')
        if target.exists() and target.stat().st_size == record['bytes'] and lab.digest(target) == record['sha256']:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.staging')
        if temporary.is_symlink():
            raise ValueError('Unsafe runtime staging temporary file')
        shutil.copy2(original, temporary)
        if temporary.stat().st_size != record['bytes'] or lab.digest(temporary) != record['sha256']:
            temporary.unlink()
            raise ValueError('Runtime copy differs from preserved disc manifest')
        temporary.replace(target)
        copied += 1
    # Migrate earlier local saves once; never replace newer runtime saves.
    old_saves, new_saves = source / 'mc0', root / 'mc0'
    if new_saves.is_symlink():
        raise ValueError('Unsafe runtime save directory')
    if old_saves.exists() and not new_saves.exists():
        if old_saves.is_symlink() or any(p.is_symlink() for p in old_saves.rglob('*')):
            raise ValueError('Unsafe original save directory')
        shutil.copytree(old_saves, new_saves)
    lab.save(private / 'analysis/runtime-storage-stage.json',
             {'local_only': True, 'disc_directory': str(root), 'file_count': len(records),
              'files_copied': copied, 'source_archive': str(source), 'saves_preserved': True})
    return root


def package_local(profile, private):
    """Create a local debug app. Never include it in the reusable source ZIP."""
    stage = json.loads((private / 'analysis/native-build-stage.json').read_text())
    binary = Path(stage['binary'])
    if lab.digest(binary) != stage['sha256']:
        raise ValueError('Native binary differs from build record')
    renderer_worker = profile.get('renderer_worker', False)
    if type(renderer_worker) is not bool:
        raise ValueError('Profile renderer_worker must be a boolean')
    async_presentation = profile.get('async_presentation', False)
    if type(async_presentation) is not bool:
        raise ValueError('Profile async_presentation must be a boolean')
    graphics_history = profile.get('graphics_history', False)
    if type(graphics_history) is not bool:
        raise ValueError('Profile graphics_history must be a boolean')
    application_storage = profile.get('application_support_storage', False)
    if type(application_storage) is not bool:
        raise ValueError('Profile application_support_storage must be a boolean')
    runtime_disc = private / 'disc-root'
    if application_storage:
        runtime_disc = stage_runtime_disc(private, Path.home() / 'Library/Application Support/PS2 Port Lab' / profile['id'] / 'disc-root')
    app = lab.ROOT.parent / (profile['title'].replace('/', '-') + ' (Debug).app')
    macos = app / 'Contents/MacOS'
    resources = app / 'Contents/Resources'
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lab.ROOT / 'CONTROLS.md', resources / 'CONTROLS.md')
    staged_runner = macos / 'ps2EntryRunner.next'
    shutil.copy2(binary, staged_runner)
    staged_runner.replace(macos / 'ps2EntryRunner')
    config = {'Executable': str(lab.boot_executable(runtime_disc, profile)),
              'DiscDirectory': str(runtime_disc), 'LogFile': str(private / 'logs/finder-launch.log'),
              'BuildIdentifier': stage['sha256'][:16], 'LocalDiagnostics': True,
              'RendererWorker': renderer_worker, 'AsyncPresentation': async_presentation, 'GraphicsHistoryEnabled': graphics_history,
              'GraphicsHistory': str(private / 'logs/window-graphics-history.jsonl')}
    image = cd_image(private)
    if image:
        config['CdImage'] = str(image)
    (resources / 'RunConfiguration.plist').write_bytes(plistlib.dumps(config))
    info = {'CFBundleName': profile['title'] + ' (Debug)', 'CFBundleDisplayName': profile['title'] + ' (Debug)',
            'CFBundleIdentifier': 'local.ps2portlab.' + profile['id'], 'CFBundleExecutable': 'LocalLauncher',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '0.1', 'NSHighResolutionCapable': True}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    logged(['/usr/bin/clang', '-framework', 'Foundation', lab.ROOT / 'cmake/LocalLauncher.m',
            '-o', macos / 'LocalLauncher.next'], private / 'logs/launcher-build.log')
    (macos / 'LocalLauncher.next').replace(macos / 'LocalLauncher')
    logged(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', app], private / 'logs/local-signing.log')
    lab.save(private / 'analysis/local-package-stage.json', {'app': str(app), 'local_only': True,
             'gameplay_verified': False, 'renderer_worker': renderer_worker, 'graphics_history': graphics_history,
             'build_sha256': stage['sha256'], 'requires_private_disc_directory': str(runtime_disc),
             'requires_private_cd_image': str(image) if image else None})
    print(f'Local debug app: {app}')
    print('Contains generated game code. Keep local; do not publish or include in the source ZIP.')
