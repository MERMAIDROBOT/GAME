"""Public runtime provisioning and local per-game builds for PS2 Port Lab."""
import contextlib
from pathlib import Path
import json
import re
import os
import subprocess
import time
import plistlib
import platform
import sys
import shutil
import pipeline as lab
from compute_slot import exclusive,assert_idle,slot

PREFIX = lab.WORK / 'runtime-deps'
PUBLIC_BUILD = lab.WORK / 'build/runtime'


def runtime_environment():
    env = os.environ.copy()
    env['PATH'] = str(lab.ENV / 'bin') + os.pathsep + env.get('PATH', '')
    env['PKG_CONFIG_PATH'] = str(PREFIX / 'lib/pkgconfig')
    return env


def logged(command, log, cwd=None, timeout=None, allow_failure=False, env_patch=None, base_environment=None):
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as f:
        try:
            environment = runtime_environment() if base_environment is None else dict(base_environment)
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


@exclusive('runtime-bootstrap')
def bootstrap_runtime():
    """Download/build public dependencies without using any game input."""
    assert_idle()
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


GRAPHICS_PLUGIN = 'liblocal-native-gs-plugin.dylib'


def build_native_graphics(profile, private):
    """Build the GPU GS renderer (runtime/gpu/native-plugin) beside the game when the profile asks for it.

    Every input is in this repository (vendored Vulkan headers, generated loader, committed SPIR-V) or pinned in
    the profile (MoltenVK, a platform SDK staged in private storage), so nothing is fetched. Returns the frameworks
    to bundle: the plugin and MoltenVK, bound by native_launch.framework_bindings."""
    spec = profile.get('native_graphics')
    if not spec:
        return {}
    if not isinstance(spec, dict) or set(spec) - {'platform_sdks'}:
        raise ValueError('native_graphics takes only platform_sdks')
    from platform_sdks import frameworks as sdk_frameworks
    sdks = sdk_frameworks(spec, private)
    if 'libMoltenVK.dylib' not in sdks and platform.system() == 'Darwin':
        raise ValueError('native_graphics on macOS needs a pinned MoltenVK platform SDK')
    for check in (['tools/gen_vk_loader.py', '--check'], ['tools/gen_shaders.py', '--check']):
        subprocess.run([sys.executable, str(lab.ROOT / check[0]), check[1]], check=True)
    build = private / 'native-graphics-build'
    command = [lab.ENV / 'bin/cmake', '-S', lab.ROOT / 'runtime/gpu/native-plugin', '-B', build, '-G', 'Ninja',
               f'-DCMAKE_MAKE_PROGRAM={lab.ENV / "bin/ninja"}', '-DCMAKE_BUILD_TYPE=Release',
               f'-DPS2_RUNTIME_SOURCE={lab.SOURCE / "ps2xRuntime"}', '-DFETCHCONTENT_FULLY_DISCONNECTED=ON']
    logged(command, private / 'logs/native-graphics-configure.log')
    logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2_native_plugin', '-j', '2'],
           private / 'logs/native-graphics-build.log')
    plugin = build / GRAPHICS_PLUGIN
    if not plugin.is_file():
        raise RuntimeError('GPU renderer build reported success without the plugin')
    from native_components import binary_dependencies
    binary_dependencies(plugin)  # system frameworks only; no reference backend
    sources = {name: lab.digest(lab.ROOT / name) for name in lab.SOURCE_FILES if name.startswith('runtime/gpu/')}
    import hashlib
    inventory = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    return {GRAPHICS_PLUGIN: {'path': str(plugin), 'sha256': lab.digest(plugin), 'engine_sources_sha256': inventory}, **sdks}


def build_native(profile, private):
    # Refuse active play before expensive source validation; builders retain their own locks.
    assert_runtime_slot_idle()
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
    if profile.get('native_source_graph') is not None:
        from native_graph import build as build_source_graph
        result=build_source_graph(profile,private)
        lab.save(private/'analysis/native-build-stage.json',result)
        print('Pinned native source graph built. Conformance and gameplay remain unverified.')
        return
    with slot('native-cmake-build'):
        assert_idle()
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
            f'-DPS2_PORT_OVERLAY_DIR={overlay_build_directory(profile, private)}',
            '-DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=16']
        record = private / 'analysis/native-build-stage.json'
        record.unlink(missing_ok=True)
        with private_patches_applied(profile, private) as title_patches:
            logged(command, private / 'logs/native-configure.log')
            logged([lab.ENV / 'bin/cmake', '--build', build, '--target', 'ps2EntryRunner', 'ps2PortVuRecompiler', '-j', '2'], private / 'logs/native-build.log')
        binary = build / 'ps2xRuntime/ps2EntryRunner'
        if not binary.exists():
            raise RuntimeError('Native build reported success without the runner binary')
        frameworks = build_native_graphics(profile, private)
        lab.save(record, {'success': True, 'binary': str(binary), 'sha256': lab.digest(binary), 'frameworks': frameworks,
                         'runtime_verified': False, 'built_at': time.time(),
                         'vu_execution_supported':True,'compiled_vu':vu_record,
                         'runtime_patches': {name: lab.digest(lab.ROOT / name) for name in RUNTIME_PATCHES},
                         'private_source_patches': {p.name: lab.digest(p) for p in title_patches}})
        print('Native runner linked locally. Gameplay remains unverified.')



def assert_runtime_slot_idle():
    assert_idle()


@exclusive('runtime-tests')
def test_runtime(profile, private):
    """Run synthetic runtime behavior checks without retail game code."""
    assert_runtime_slot_idle()
    record=private/'analysis/native-build-stage.json'
    stage=json.loads(record.read_text()) if record.is_file() else {}
    if stage.get('source_graph_recipe_sha256'):
        # A failed rerun must revoke prior verification before doing any work.
        stage['runtime_verified']=False
        lab.save(record,stage)
        test_record=private/'analysis/runtime-tests-stage.json'
        lab.save(test_record,dict(success=False,status='running',gameplay_verified=False))
        tests=stage.get('graph_tests',{})
        if not tests:raise ValueError('Native graph has no conformance test artifacts')
        # Developer launch switches must not silently disable the behavior tested.
        environment={k:v for k,v in runtime_environment().items()
                     if not k.startswith(('PS2_PORT_','PS2X_')) and k not in
                     {'DYLD_INSERT_LIBRARIES','DYLD_LIBRARY_PATH','LD_PRELOAD','LD_LIBRARY_PATH'}}
        from native_graph import conformance_cases
        cases=conformance_cases(stage.get('graph_test_cases',[]))
        if cases and not re.fullmatch('[a-f0-9]{64}',stage.get('sha256','')):raise ValueError('Conformance cases require a pinned runner digest')
        log_root=private/'logs'/'source-conformance'/stage['sha256'] if cases else private/'logs'
        observed={}
        for name,artifact in tests.items():
            from clean_build import ID
            if not isinstance(name,str) or not ID.fullmatch(name):
                raise ValueError('Invalid source graph conformance target name')
            binary=Path(artifact['path'])
            if binary.is_symlink() or not binary.resolve().is_relative_to(private.resolve()) or lab.digest(binary)!=artifact['sha256']:
                raise ValueError('Conformance executable differs from source build receipt')
            log=log_root/('graph-test-'+name+'.log')
            case_results=[]
            for case in cases or [dict(name='default',environment={})]:
                case_log=log_root/('graph-test-'+name+'-'+case['name']+'.log') if cases else log
                if logged([binary],case_log,timeout=120,base_environment={**environment,**case['environment']})!='success':
                    raise RuntimeError('Source graph conformance test timed out')
                if lab.digest(binary)!=artifact['sha256']:
                    raise ValueError('Conformance executable changed during execution')
                case_results.append(dict(name=case['name'],environment=case['environment'],log=str(case_log),log_sha256=lab.digest(case_log)))
            if cases:
                log.write_text('\n'.join('CASE '+case['name']+'\n'+Path(case['log']).read_text(errors='replace') for case in case_results))
            observed[name]=dict(sha256=artifact['sha256'],log=str(log),log_sha256=lab.digest(log))
            if cases:observed[name]['cases']=case_results
        lab.save(test_record,dict(success=True,source_graph_recipe_sha256=stage['source_graph_recipe_sha256'],
                                 runner_sha256=stage['sha256'],tests=observed,test_cases=cases,gameplay_verified=False))
        stage['runtime_verified']=True
        stage['conformance_receipt']=dict(path=str(test_record),sha256=lab.digest(test_record))
        lab.save(record,stage)
        return
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
    for component in ('test_native_audio.py', 'test_direction_recovery.py', 'test_gpu_components.py', 'test_frame_ownership.py'):
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


def overlay_build_directory(profile, private):
    """private/overlays when the profile opts in (`overlay_build: true`) and overlays were registered; else ''."""
    if profile.get('overlay_build', False) is not True:
        return ''
    record = Path(private) / 'analysis/overlay-translation.json'
    if not record.is_file() or not json.loads(record.read_text()).get('accepted_overlays'):
        raise ValueError('overlay_build is set but no overlays were registered; run translate-overlays')
    if not json.loads(record.read_text()).get('syntax_sample', {}).get('success'):
        raise ValueError('Overlay sources have not passed the syntax sample; run translate-overlays')
    return str(Path(private) / 'overlays')


# Applied in order over the pinned PS2Recomp revision. The IOP patch carries the SPU2 device,
# scheduler conformance (RPC servers honour thread priority, blocking RPC, alarms), AutoDMA and
# their CTest suites; it stacks on the diagnostics patch. Title conformance carries the generic
# kernel/IOP/CD/GS/MPEG behaviour a second title needed (see ENGINE.md "Runtime title conformance").
RUNTIME_PATCHES = ('patches/runtime-diagnostics.patch', 'patches/ps2xIOP-port-lab.patch', 'patches/scalar-fpu.patch',
                   'patches/runtime-title-conformance.patch', 'patches/scalar-fpu-compare.patch',
                   'patches/runtime-hotpaths.patch', 'patches/native-pcm.patch','patches/host-input.patch','patches/ee64-varargs.patch','patches/sdk-audio-policy.patch','patches/gif-image2.patch', 'patches/mpeg-stream-callbacks.patch','patches/host-input-events.patch','patches/touch-direction-recovery.patch','patches/native-save-root.patch','patches/host-clock-accounting.patch','patches/native-callback-context-lifetime.patch','patches/overlay-dispatch.patch','patches/stub-interior-entries.patch','patches/mmi-reference-forms.patch','patches/vu0-reference-forms.patch','patches/link-branch-reference.patch','patches/fpu-reference-flags.patch','patches/vu-micro-reference.patch','patches/gs-host-rounding.patch','patches/vu-run-to-end.patch','patches/vu-vf0-constant.patch','patches/iop-shared-pool.patch','patches/presentation-fidelity.patch','patches/gpu-renderer-host.patch','patches/window-thread-failure.patch','patches/hd-frame-stride.patch','patches/vsync-setting.patch')


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
            if line.startswith(('+++ b/','--- a/'))]


def _stack_already_applied(patches, checked_paths=None, compare_worktree=True):
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
        if not compare_worktree:
            return True
        paths = checked_paths if checked_paths is not None else sorted({path for patch in patches for path in _patch_paths(patch)})
        unknown = subprocess.run([*git, 'ls-files', '--others', '--', *paths],env=env,capture_output=True)
        if unknown.returncode or unknown.stdout.strip():
            return False
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
    recorded = sorted(record.glob('*.patch')) if record.exists() else []
    paths=sorted({path for patch in [*recorded,*patches] for path in _patch_paths(patch)})
    # Qualify the complete new stack and the exact current state BEFORE peeling
    # any old patch. A failed refresh must not strip a working checkout's fixes.
    if not _stack_already_applied(patches,compare_worktree=False):
        raise RuntimeError('Runtime patch stack does not apply to pinned HEAD; source checkout left unchanged')
    old=recorded if recorded and _stack_already_applied(recorded,checked_paths=paths) else []
    if not old and not _stack_already_applied([],checked_paths=paths):
        raise RuntimeError('Unknown tool source edits; checkout left unchanged. Use isolated locked tool sources.')
    removed=[];applied=[]
    try:
        for patch in reversed(old):
            subprocess.run(['git','-C',str(lab.SOURCE),'apply','--reverse',str(patch)],check=True);removed.append(patch)
        for patch in patches:
            subprocess.run(['git','-C',str(lab.SOURCE),'apply',str(patch)],check=True);applied.append(patch)
    except Exception:
        for patch in reversed(applied):subprocess.run(['git','-C',str(lab.SOURCE),'apply','--reverse',str(patch)],check=True)
        for patch in reversed(removed):subprocess.run(['git','-C',str(lab.SOURCE),'apply',str(patch)],check=True)
        raise
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


@exclusive('boot-probe')
def boot_probe(profile, private, headless=False):
    """Bounded first-boot observation; no claim of gameplay correctness."""
    private=Path(private).resolve()
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
    assert_idle()
    elf = lab.boot_executable(private / 'disc-root', profile)
    name = 'headless-probe' if headless else 'boot-probe'
    log = private / f'logs/{name}.log'
    frame = private / 'logs/headless-frame.ppm'
    if headless:
        frame.unlink(missing_ok=True)
    from native_graph import framework_inputs
    from launch_config import prepare as prepare_launch,execution_environment
    frameworks=framework_inputs(stage,private)
    launch=prepare_launch(profile,private,frameworks)
    environment=execution_environment(profile,launch,frameworks,private)
    environment['PS2_PORT_IO_ROOT']=str((private/'disc-root').resolve())
    image = cd_image(private)
    if image:
        environment['PS2_PORT_CD_IMAGE'] = str(image)
    probe_overrides={}
    if headless:
        probe_overrides.update(PS2_PORT_HEADLESS='1', PS2_PORT_MPEG_TRACE='1',
                           PS2_PORT_FRAME=str(private / 'logs/headless-frame.ppm'),
                           PS2_PORT_GS_HISTORY=str(private / 'logs/graphics-history.jsonl'))
        environment.update(probe_overrides)
    clean_environment={k:v for k,v in runtime_environment().items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
    outcome = logged([binary, elf], log, cwd=private / 'disc-root', timeout=30, allow_failure=True,
                     env_patch=environment,base_environment=clean_environment)
    from port_checks import audit
    qualification=audit(log=log)
    qualification_path=private/f'analysis/{name}-qualification.json'
    lab.save(qualification_path,qualification)
    probe_qualified=qualification['structural_checks_passed'] and not outcome.startswith('exit_')
    lab.save(private / f'analysis/{name}-stage.json', {'outcome': outcome, 'effective_environment':environment, 'probe_overrides':probe_overrides, 'launch_assets':launch['assets'], 'launch_scope':'Declared renderer and settings with isolated private disc/save root; headless diagnostic overrides explicitly listed', 'build_stage_sha256':lab.digest(private/'analysis/native-build-stage.json'), 'binary_sha256':lab.digest(binary), 'executable_sha256':lab.digest(elf), 'toolchain_lock_sha256':lab.digest(lab.ROOT/'toolchain.lock.json'), 'probe_qualified':probe_qualified, 'diagnostics_passed':qualification['structural_checks_passed'], 'log_sha256':lab.digest(log), 'qualification':str(qualification_path), 'qualification_sha256':lab.digest(qualification_path), 'duration_limit_seconds': 30, 'cd_image': str(image) if image else None,
             'gameplay_verified': False, 'log': str(log), 'headless': headless, 'frame_capture': str(frame) if headless and frame.exists() else None})
    if not probe_qualified:
        if any(item['code']=='guest_target_missing' for item in qualification['findings']):
            from feedback import plan_boot_recovery
            try:recovery=plan_boot_recovery(profile,private,f'analysis/{name}-stage.json')
            except (OSError,ValueError,KeyError) as error:
                recovery=dict(status='blocked',reason=str(error),automatic_translation_allowed=False,gameplay_verified=False)
            lab.save(private/f'analysis/{name}-recovery.json',recovery)
        raise RuntimeError('Boot probe failed runtime qualification; inspect its pinned local log and qualification report')
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
        # An APFS clone shares the original's blocks (no extra space); any other volume gets a real copy.
        if platform.system() != 'Darwin' or subprocess.run(['/bin/cp', '-c', str(original), str(temporary)],
                                                           capture_output=True).returncode:
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


def verify_graph_conformance(stage, private):
    """Bind packaging to successful checks of this exact source-built runner."""
    from native_graph import verify_source_chain
    verify_source_chain(stage,private)
    if stage.get('runtime_verified') is not True:
        raise ValueError('Run source graph conformance before packaging')
    pinned=stage.get('conformance_receipt',{})
    receipt=Path(pinned.get('path',''))
    if receipt.is_symlink() or not receipt.is_file() or not receipt.resolve().is_relative_to(private.resolve()) or lab.digest(receipt)!=pinned.get('sha256'):
        raise ValueError('Source graph conformance receipt is missing or changed')
    checks=json.loads(receipt.read_text())
    if checks.get('success') is not True or checks.get('source_graph_recipe_sha256')!=stage['source_graph_recipe_sha256'] or checks.get('runner_sha256')!=stage['sha256']:
        raise ValueError('Conformance receipt does not certify this source build')
    from native_graph import conformance_cases
    cases=conformance_cases(stage.get('graph_test_cases',[]))
    if checks.get('test_cases',[])!=cases:raise ValueError('Conformance receipt omits declared runtime cases')
    artifacts=stage.get('graph_tests',{})
    if not artifacts or set(checks.get('tests',{}))!=set(artifacts):
        raise ValueError('Conformance receipt does not cover every source test')
    for name,artifact in artifacts.items():
        binary=Path(artifact['path']);observed=checks['tests'][name]
        if binary.is_symlink() or not binary.resolve().is_relative_to(private.resolve()) or lab.digest(binary)!=artifact['sha256'] or observed.get('sha256')!=artifact['sha256']:
            raise ValueError('Source graph conformance binary changed after verification')
        log=Path(observed.get('log',''))
        if log.is_symlink() or not log.is_file() or not log.resolve().is_relative_to(private.resolve()) or lab.digest(log)!=observed.get('log_sha256'):
            raise ValueError('Source graph conformance log is missing or changed')
        if cases:
            observed_cases=observed.get('cases',[])
            if [dict(name=c.get('name'),environment=c.get('environment')) for c in observed_cases]!=cases:
                raise ValueError('Conformance receipt omits declared runtime cases')
            for case in observed_cases:
                case_log=Path(case.get('log',''))
                if case_log.is_symlink() or not case_log.is_file() or not case_log.resolve().is_relative_to(private.resolve()) or lab.digest(case_log)!=case.get('log_sha256'):
                    raise ValueError('Conformance case log is missing or changed')


def package_local(profile, private):
    """Create a local debug app. Never include it in the reusable source ZIP."""
    stage = json.loads((private / 'analysis/native-build-stage.json').read_text())
    binary = Path(stage['binary'])
    if lab.digest(binary) != stage['sha256']:
        raise ValueError('Native binary differs from build record')
    app = lab.ROOT.parent / (profile['title'].replace('/', '-') + ' (Debug).app')
    if app.exists() or app.is_symlink():
        raise ValueError('Package destination already exists; preserve it and choose a new isolated profile title/output')
    from native_graph import framework_inputs
    frameworks=framework_inputs(stage,private)
    if stage.get('source_graph_recipe_sha256'):
        verify_graph_conformance(stage,private)
    from launch_config import prepare as prepare_launch
    launch=prepare_launch(profile,private,frameworks)
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
    # An app opened from Finder is its own privacy subject: reading or writing ~/Documents waits on a permission
    # prompt. Application Support storage keeps the disc, log, graphics history and GPU cache outside it.
    storage = private
    if application_storage:
        storage = Path.home() / 'Library/Application Support/PS2 Port Lab' / profile['id']
        runtime_disc = stage_runtime_disc(private, storage / 'disc-root')
        for folder in ('logs', 'cache/gpu-pipelines'):
            (storage / folder).mkdir(parents=True, exist_ok=True)
        if 'PS2_PORT_HW_GS_PIPELINE_CACHE_DIR' in launch['Environment']:
            launch['Environment']['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR'] = str(storage / 'cache/gpu-pipelines')
    macos = app / 'Contents/MacOS'
    resources = app / 'Contents/Resources'
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    if frameworks:
        folder=app/'Contents/Frameworks';folder.mkdir()
        for name,path in frameworks.items():shutil.copy2(path,folder/name)
    shutil.copy2(lab.ROOT / 'CONTROLS.md', resources / 'CONTROLS.md')
    staged_runner = macos / 'ps2EntryRunner.next'
    shutil.copy2(binary, staged_runner)
    staged_runner.replace(macos / 'ps2EntryRunner')
    config = {'Executable': str(lab.boot_executable(runtime_disc, profile)),
              'DiscDirectory': str(runtime_disc), 'LogFile': str(storage / 'logs/finder-launch.log'),
              'BuildIdentifier': stage['sha256'][:16], 'LocalDiagnostics': True,
              'RendererWorker': renderer_worker, 'AsyncPresentation': async_presentation, 'GraphicsHistoryEnabled': graphics_history,
              'GraphicsHistory': str(storage / 'logs/window-graphics-history.jsonl')}
    config.update({key:value for key,value in launch.items() if key!='assets'})
    capabilities={}
    declared_capabilities=stage.get('runtime_capabilities',{})
    if not isinstance(declared_capabilities,dict) or set(declared_capabilities)-{'separate_save_root'} or any(v is not True for v in declared_capabilities.values()):raise ValueError('Unknown source runtime capability')
    if declared_capabilities:
        if not stage.get('source_graph_recipe_sha256'):raise ValueError('Runtime capabilities require a source-built runner')
        from native_graph import runtime_capabilities
        record=stage.get('source_build_receipt') or {}
        receipt=private/record.get('path','')
        if not receipt.is_file() or lab.digest(receipt)!=record.get('sha256'):raise ValueError('Source build capability receipt changed')
        built=json.loads(receipt.read_text())
        capabilities=runtime_capabilities(stage['source_graph_recipe'],built,stage.get('runner_source_units',[]))
        if capabilities!=stage.get('runtime_capabilities',{}):raise ValueError('Source runtime capability changed')
    config['RuntimeCapabilities']=capabilities
    manifest=private/'disc-manifest.json'
    config['DiscManifest']={'path':str(manifest),'sha256':lab.digest(manifest)}
    config['AssetPins']=launch['assets']

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
             'build_sha256': lab.digest(macos/'ps2EntryRunner'), 'source_build_sha256':stage['sha256'], 'configuration_sha256':lab.digest(resources/'RunConfiguration.plist'), 'launcher_sha256':lab.digest(macos/'LocalLauncher'), 'info_sha256':lab.digest(app/'Contents/Info.plist'), 'launch_assets':launch['assets'], 'frameworks':{name:lab.digest(app/'Contents/Frameworks'/name) for name in frameworks}, 'source_frameworks':{name:lab.digest(path) for name,path in frameworks.items()}, 'requires_private_disc_directory': str(runtime_disc),
             'requires_private_cd_image': str(image) if image else None})
    print(f'Local debug app: {app}')
    print('Contains generated game code. Keep local; do not publish or include in the source ZIP.')
