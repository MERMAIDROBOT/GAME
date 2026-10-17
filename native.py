"""Public runtime provisioning and local per-game builds for PS2 Port Lab."""
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
    logged(command, private / 'logs/native-configure.log')
    logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2EntryRunner', 'ps2PortVuRecompiler', '-j', '2'], private / 'logs/native-build.log')
    binary = build / 'ps2xRuntime/ps2EntryRunner'
    if not binary.exists():
        raise RuntimeError('Native build reported success without the runner binary')
    lab.save(record, {'success': True, 'binary': str(binary), 'sha256': lab.digest(binary),
                     'runtime_verified': False, 'built_at': time.time(),
                     'vu_execution_supported':True,'compiled_vu':vu_record})
    print('Native runner linked locally. Gameplay remains unverified.')



def test_runtime(profile, private):
    """Run synthetic runtime behavior checks without retail game code."""
    build = private / 'native-build'
    if not (build / 'build.ninja').exists():
        raise ValueError('Run build-native to configure the local runtime first')
    logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2PortRuntimeTests', 'ps2PortVuTests', 'ps2PortHostPadTests', 'ps2PortPresentationTests', 'ps2PortIopImportTests', '-j', '2'],
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
    print('Synthetic runtime in synchronous/worker modes, IOP imports, host input/presentation, 43 VU and 45 texture, palette and VRAM checks passed; no retail code executed.')


def apply_runtime_patch():
    patch = lab.ROOT / 'patches/runtime-diagnostics.patch'
    if not patch.exists():
        return
    reverse = subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', '--reverse', '--check', str(patch)], capture_output=True)
    if reverse.returncode == 0:
        return
    subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(lab.SOURCE), 'apply', str(patch)], check=True)


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
    if headless:
        environment.update(PS2_PORT_HEADLESS='1', PS2_PORT_MPEG_TRACE='1',
                           PS2_PORT_FRAME=str(private / 'logs/headless-frame.ppm'),
                           PS2_PORT_GS_HISTORY=str(private / 'logs/graphics-history.jsonl'))
    outcome = logged([binary, elf], log, cwd=private / 'disc-root', timeout=30, allow_failure=True,
                     env_patch=environment)
    lab.save(private / f'analysis/{name}-stage.json', {'outcome': outcome, 'duration_limit_seconds': 30,
             'gameplay_verified': False, 'log': str(log), 'headless': headless, 'frame_capture': str(frame) if headless and frame.exists() else None})
    print(f'First boot probe: {outcome}. Inspect local diagnostics; no gameplay claim.')


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
             'build_sha256': stage['sha256'], 'requires_private_disc_directory': str(runtime_disc)})
    print(f'Local debug app: {app}')
    print('Contains generated game code. Keep local; do not publish or include in the source ZIP.')
