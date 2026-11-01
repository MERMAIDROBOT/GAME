"""Keep analyzer/recompiler binaries synchronized with carried source policies.

An engine update must not silently keep using a compiler built before its fixes.
Offline refresh uses only existing locked public source trees and build tools.
"""
from pathlib import Path
import hashlib,json,os,subprocess
from compute_slot import exclusive,assert_idle
from clean_build import sha,filesystem_metadata

EXT={'.c','.cc','.cpp','.cxx','.h','.hh','.hpp','.hxx','.inl','.inc','.S','.cmake','.in','.def','.py','.sh','.y','.l','.json','.toml','.yaml','.yml','.glsl','.vert','.frag','.comp'}
COMPILER_SETTINGS=('CMAKE_BUILD_TYPE=Release','PS2X_BUILD_RUNTIME=OFF','PS2X_BUILD_TEST=OFF','PS2X_BUILD_STUDIO=OFF')

def configure_command(lab,*,offline=False):
 bindir=lab.ENV/('Scripts' if os.name=='nt' else 'bin')
 args=[bindir/'cmake','-S',lab.SOURCE,'-B',lab.BUILD,'-G','Ninja',f'-DCMAKE_MAKE_PROGRAM={bindir / "ninja"}']
 args+=['-D'+s for s in COMPILER_SETTINGS]
 if offline:args+=['-DFETCHCONTENT_FULLY_DISCONNECTED=ON','-DFETCHCONTENT_UPDATES_DISCONNECTED=ON']
 return args


def tree_pin(root):
 root=Path(root);files={}
 for p in sorted(root.rglob('*')):
  if '.git' in p.relative_to(root).parts or not (p.suffix in EXT or p.name=='CMakeLists.txt'):continue
  if p.is_file() and filesystem_metadata(p):continue
  if p.is_symlink():raise ValueError('Compiler source symlink requires explicit review: '+str(p))
  if p.is_file():files[str(p.relative_to(root))]=sha(p)
 if not files:raise ValueError('Compiler source tree is empty: '+str(root))
 return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def inputs(lab):
 from native import RUNTIME_PATCHES
 revision=subprocess.check_output(['git','-C',str(lab.SOURCE),'rev-parse','HEAD'],text=True).strip()
 if revision!=lab.LOCK['ps2recomp']['revision']:raise ValueError('Compiler source revision differs from lock')
 dependencies={}
 for name,dependency_revision in lab.LOCK.get('resolved_dependencies',{}).items():
  root=lab.BUILD/'_deps'/(name+'-src')
  if not root.is_dir():raise RuntimeError('Locked public dependency missing; run bootstrap: '+name)
  actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
  if actual!=dependency_revision:raise ValueError('Compiler dependency revision differs from lock: '+name)
  dependencies[name]=dict(revision=actual,source_sha256=tree_pin(root))
 return dict(compiler_settings=list(COMPILER_SETTINGS),source_revision=revision,lock_sha256=sha(lab.ROOT/'toolchain.lock.json'),source_sha256=tree_pin(lab.SOURCE),patches={name:sha(lab.ROOT/name) for name in RUNTIME_PATCHES},dependencies=dependencies)


def state(lab):
 return dict(schema=1,inputs=inputs(lab),tools={name:sha(lab.BUILD/path) if (lab.BUILD/path).is_file() else None for name,path in [('analyzer','ps2xAnalyzer/ps2_analyzer'),('recompiler','ps2xRecomp/ps2_recomp')]})


def matches(lab):
 receipt=lab.BUILD/'port-tool-state.json'
 if not receipt.is_file():return False
 try:prior=json.loads(receipt.read_text());return prior==state(lab) and all(prior.get('tools',{}).values())
 except (ValueError,OSError):return False


def record(lab,expected_inputs):
 current=state(lab)
 if current['inputs']!=expected_inputs or not all(current['tools'].values()):raise RuntimeError('Compiler source/policies changed during build or products are missing')
 path=lab.BUILD/'port-tool-state.json';temporary=path.with_suffix('.next.json');temporary.write_text(json.dumps(current,indent=2)+'\n');temporary.replace(path)
 return current


@exclusive('compiler-refresh')
def refresh(lab):
 if matches(lab):return
 assert_idle()
 bindir=lab.ENV/('Scripts' if os.name=='nt' else 'bin');cmake=bindir/'cmake';ninja=bindir/'ninja'
 if not cmake.is_file() or not ninja.is_file():raise RuntimeError('Build tools missing; run public-tool bootstrap first')
 # Validate all sources before configuring: fully-disconnected CMake must never
 # fetch a missing dependency while processing a private game.
 inputs(lab)
 from native import apply_runtime_patch
 apply_runtime_patch()
 # Patches may create new files with exFAT metadata after initial checkout.
 # Only an explicitly engine-owned isolation receipt permits cleanup.
 from offline_toolchain import clean_isolated_metadata
 clean_isolated_metadata(lab)
 expected=inputs(lab)
 logs=lab.WORK/'build-logs'
 lab.run(configure_command(lab,offline=True),logs/'compiler-refresh-configure.log')
 lab.run([cmake,'--build',lab.BUILD,'--target','ps2_analyzer','ps2_recomp','-j','2'],logs/'compiler-refresh-build.log')
 record(lab,expected)


def ensure(lab):
 if not matches(lab):refresh(lab)
