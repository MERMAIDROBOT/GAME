"""Source-owned native graphics provisioning/building without private game input."""
from pathlib import Path
import hashlib,json,os,re,subprocess,shutil
from clean_build import sha,filesystem_metadata
from compute_slot import exclusive,assert_idle
from tool_state import tree_pin


URLS={'volk':'https://github.com/zeux/volk.git','vulkan_headers':'https://github.com/KhronosGroup/Vulkan-Headers.git','glslang':'https://github.com/KhronosGroup/glslang.git'}


def build_environment():
 # CMake otherwise silently imports flags, search roots or an unrelated toolchain
 # from the caller's shell, even for a brand new pinned source build.
 removed={'CC','CXX','CPP','AR','LD','CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS',
  'CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','SDKROOT',
  'MACOSX_DEPLOYMENT_TARGET','COMPILER_PATH','GCC_EXEC_PREFIX','CCC_OVERRIDE_OPTIONS',
  'DYLD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES','LD_LIBRARY_PATH','LD_PRELOAD',
  'CMAKE_PREFIX_PATH','CMAKE_LIBRARY_PATH','CMAKE_INCLUDE_PATH','CMAKE_TOOLCHAIN_FILE',
  'CMAKE_GENERATOR','CMAKE_PROJECT_INCLUDE','CMAKE_PROJECT_INCLUDE_BEFORE'}
 return {k:v for k,v in os.environ.items() if k not in removed and not k.startswith(('PS2_PORT_','PS2HOST_','PS2X_'))}


def sources(lab,overrides=None):
 overrides=overrides or {}
 if set(overrides)-URLS.keys():raise ValueError('Unknown native graphics source override')
 result={}
 for name,url in URLS.items():
  entry=lab.LOCK.get('native_graphics_sources',{}).get(name,{})
  if entry.get('url')!=url or not re.fullmatch('[a-f0-9]{40}',entry.get('revision','')):raise ValueError('Missing/invalid locked native graphics source: '+name)
  root=Path(overrides.get(name,lab.WORK/'dependencies'/('native-'+name))).resolve()
  if not root.is_dir():raise ValueError('Native graphics source missing; run bootstrap-native-graphics: '+name)
  revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
  if revision!=entry['revision']:raise ValueError('Native graphics source revision differs: '+name)
  result[name]=dict(path=str(root),revision=revision,source_sha256=tree_pin(root))
 return result


def guard():
 assert_idle()


def runtime_source(lab):
 """The adapter's memory ABI must come from the locked runtime, not any checkout."""
 expected=lab.LOCK.get('ps2recomp',{}).get('revision','')
 if not re.fullmatch('[a-f0-9]{40}',expected):raise ValueError('Missing locked runtime source revision')
 actual=subprocess.check_output(['git','-C',str(lab.SOURCE),'rev-parse','HEAD'],text=True).strip()
 if actual!=expected:raise ValueError('Native adapter runtime revision differs from lock')
 runtime=lab.SOURCE/'ps2xRuntime'
 if not (runtime/'src/lib/gs/ps2_gs_memory.cpp').is_file():raise ValueError('Matching public runtime source is missing')
 return dict(revision=actual,source_sha256=tree_pin(runtime))


def provision(lab,private,configuration):
 """Build a declared native component automatically; never import old binaries."""
 if not isinstance(configuration,dict) or set(configuration)!={'sources'}:raise ValueError('Native component build must declare pinned local sources')
 declared=configuration['sources']
 if not isinstance(declared,dict) or set(declared)!=set(URLS):raise ValueError('Declare all locked native graphics sources')
 overrides={}
 for name,entry in declared.items():
  if not isinstance(entry,dict) or set(entry)!={'path','sha256'} or not isinstance(entry['path'],str) or not Path(entry['path']).is_absolute() or not isinstance(entry['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):raise ValueError('Native source needs an absolute path and source pin')
  overrides[name]=entry['path']
 resolved=sources(lab,overrides)
 if any(resolved[name]['source_sha256']!=entry['sha256'] for name,entry in declared.items()):raise ValueError('Declared native component source pin changed')
 environment=build_environment();bindir=lab.ENV/('Scripts' if os.name=='nt' else 'bin')
 paths=[bindir/'cmake',bindir/'ninja',*(shutil.which(n,path=environment.get('PATH')) for n in ['clang','clang++'])]
 if any(not p or not Path(p).is_file() for p in paths):raise ValueError('Native component build tools are missing')
 contract=dict(sources=resolved,runtime_source=runtime_source(lab),builder_sha256=sha(__file__),lock_sha256=sha(lab.ROOT/'toolchain.lock.json'),
  engine_sources={n:sha(lab.ROOT/n) for n in lab.SOURCE_FILES if n.startswith(('runtime/gpu/','runtime/presentation/'))},
  tools={str(Path(p).resolve()):sha(Path(p).resolve()) for p in paths})
 key=hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 private=Path(private).resolve();base=private/'native-components'/key
 if (private/'native-components').is_symlink() or base.is_symlink():raise ValueError('Unsafe component build cache')
 base.mkdir(parents=True,exist_ok=True)
 from clean_build import path_in
 for receipt in sorted(base.glob('attempt-*/receipt.json')):
  receipt=path_in(private,str(receipt.relative_to(private)))
  record=json.loads(receipt.read_text())
  if record.get('status')=='native_graphics_source_build_passed':
   return verify(lab,receipt)
 # Preserve failed attempts. One new build per invocation; no retry loop that
 # could fill the drive after a persistent compiler error.
 for attempt in range(100):
  output=path_in(private,str((base/('attempt-'+str(attempt).zfill(3))).relative_to(private)))
  if not output.exists():break
 else:raise ValueError('Too many failed native component attempts; review local receipts')
 build(lab,output,overrides=overrides,execute=True)
 artifact=verify(lab,output/'receipt.json')
 # Refuse success if a declared source was edited during provisioning.
 if sources(lab,overrides)!=resolved:raise ValueError('Native sources changed during provisioning')
 return artifact


@exclusive('native-graphics-bootstrap')
def bootstrap(lab):
 guard()
 for name,url in URLS.items():
  ref=lab.LOCK.get('native_graphics_sources',{}).get(name,{})
  if ref.get('url')!=url or not re.fullmatch('[a-f0-9]{40}',ref.get('revision','')):raise ValueError('Invalid public graphics dependency lock')
  root=lab.WORK/'dependencies'/('native-'+name)
  if root.exists():continue # Never reset an existing checkout to make it match.
  root.mkdir(parents=True)
  subprocess.run(['git','init',str(root)],check=True,capture_output=True)
  subprocess.run(['git','-C',str(root),'remote','add','origin',url],check=True)
  subprocess.run(['git','-C',str(root),'fetch','--depth','1','origin',ref['revision']],check=True)
  subprocess.run(['git','-C',str(root),'checkout','--detach','FETCH_HEAD'],check=True)
 return sources(lab)


def binary_dependencies(path):
 import platform
 if platform.system()!='Darwin':return dict(inspected=False)
 output=subprocess.check_output(['/usr/bin/otool','-L',str(path)],text=True)
 names=[line.strip().split(' (',1)[0] for line in output.splitlines()[1:]]
 external=[n for n in names if Path(n).name!=Path(path).name and not n.startswith(('/System/Library/','/usr/lib/'))]
 symbols=subprocess.check_output(['/usr/bin/nm','-u',str(path)],text=True)
 if external or any(name in symbols for name in ['GSInterface','_ZN7Granite','_ZN6Vulkan7Context']):raise ValueError('Native adapter still depends on a non-SDK/reference backend product')
 return dict(inspected=True,dynamic_dependencies=names,reference_backend_imports=False)


def build(lab,destination,*,overrides=None,execute=False):
 output=Path(destination).resolve()
 if output.exists():raise ValueError('Preserve prior native graphics builds; choose a fresh output')
 resolved=sources(lab,overrides)
 for source in [lab.ROOT/'runtime/gpu',lab.SOURCE,*(Path(r['path']) for r in resolved.values())]:
  if output.is_relative_to(source.resolve()) or source.resolve().is_relative_to(output):raise ValueError('Native output overlaps source inputs')
 runtime=runtime_source(lab)
 source_inputs={name:sha(lab.ROOT/name) for name in lab.SOURCE_FILES if name.startswith(('runtime/gpu/','runtime/presentation/'))}
 report=dict(schema=1,status='native_graphics_planned',sources=resolved,engine_sources=source_inputs,runtime_source=runtime,lock_sha256=sha(lab.ROOT/'toolchain.lock.json'),builder_sha256=sha(__file__),historical_objects_used=False,gameplay_verified=False,source_candidate=True)
 if not execute:return report
 return execute_build(lab,output,overrides,report)


@exclusive('native-graphics-build')
def execute_build(lab,output,overrides,report):
 guard()
 from native import logged
 cmake=lab.ENV/('Scripts' if os.name=='nt' else 'bin')/'cmake';ninja=cmake.with_name('ninja')
 if not cmake.is_file() or not ninja.is_file():raise ValueError('Native build tools missing; run bootstrap first')
 environment=build_environment();compilers={name:shutil.which(binary,path=environment.get('PATH')) for name,binary in [('C','clang'),('CXX','clang++')]}
 if not all(compilers.values()):raise ValueError('Native graphics requires explicit Clang compilers')
 tools={str(Path(p).resolve()):sha(Path(p).resolve()) for p in [cmake,ninja,*compilers.values()]}
 output.mkdir(parents=True);report.update(status='running',commands=[],build_tools=tools,caller_build_flags_ignored=True)
 def save():(output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 save()
 try:
  command=[cmake,'-S',lab.ROOT/'runtime/gpu/native-plugin','-B',output/'build','-G','Ninja',f'-DCMAKE_MAKE_PROGRAM={ninja}','-DCMAKE_BUILD_TYPE=Release',f'-DPS2_RUNTIME_SOURCE={lab.SOURCE / "ps2xRuntime"}','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DFETCHCONTENT_FULLY_DISCONNECTED=ON','-DFETCHCONTENT_UPDATES_DISCONNECTED=ON']
  command += ['-DCMAKE_'+name+'_COMPILER='+path for name,path in compilers.items()]
  for name,entry in report['sources'].items():command.append('-DFETCHCONTENT_SOURCE_DIR_'+name.upper()+'='+entry['path'])
  report['commands'].append([str(a) for a in command]);save();logged(command,output/'configure.log',base_environment=environment)
  command=[cmake,'--build',output/'build','--target','ps2_native_plugin','-j','2'];report['commands'].append([str(a) for a in command]);save();logged(command,output/'build.log',base_environment=environment)
  products=[p for pattern in ['*local-native-gs-plugin.dylib','*local-native-gs-plugin.so'] for p in (output/'build').glob(pattern) if not filesystem_metadata(p)]
  if len(products)!=1:raise ValueError('Expected exactly one source-built native plugin')
  if sources(lab,overrides)!=report['sources'] or runtime_source(lab)!=report['runtime_source']:raise ValueError('Native source inputs changed during build')
  if sha(lab.ROOT/'toolchain.lock.json')!=report['lock_sha256']:raise ValueError('Native dependency lock changed during build')
  if any(sha(path)!=pin for path,pin in tools.items()):raise ValueError('Native build tool changed during compilation')
  if sha(__file__)!=report['builder_sha256']:raise ValueError('Native builder changed during compilation')
  if any(sha(lab.ROOT/name)!=pin for name,pin in report['engine_sources'].items()):raise ValueError('Engine graphics source changed during build')
  report.update(binary_dependencies=binary_dependencies(products[0]))
  report.update(status='native_graphics_source_build_passed',plugin=dict(path=str(products[0]),sha256=sha(products[0])),compile_database_sha256=sha(output/'build/compile_commands.json'));save();return report
 except Exception as error:
  report.update(status='failed',error=str(error));save();raise


def verify(lab,receipt_path):
 """Import a completed source component only while its full provenance matches."""
 raw=Path(receipt_path)
 if raw.is_symlink():raise ValueError('Unsafe native component receipt')
 receipt=raw.resolve();pin=sha(receipt);record=json.loads(receipt.read_text())
 if record.get('schema')!=1 or record.get('status')!='native_graphics_source_build_passed' or record.get('historical_objects_used') is not False:
  raise ValueError('Native component has no successful source build receipt')
 if record.get('builder_sha256')!=sha(__file__) or record.get('lock_sha256')!=sha(lab.ROOT/'toolchain.lock.json'):
  raise ValueError('Native component builder or dependency lock changed')
 expected={name:sha(lab.ROOT/name) for name in lab.SOURCE_FILES if name.startswith(('runtime/gpu/','runtime/presentation/'))}
 if record.get('engine_sources')!=expected:raise ValueError('Native component source inventory changed')
 overrides={name:entry['path'] for name,entry in record.get('sources',{}).items()}
 if sources(lab,overrides)!=record.get('sources') or runtime_source(lab)!=record.get('runtime_source'):
  raise ValueError('Native component source dependencies changed')
 tools=record.get('build_tools',{})
 if not tools or any(sha(path)!=digest for path,digest in tools.items()):raise ValueError('Native component build tools changed')
 artifact=record.get('plugin',{});binary=Path(artifact.get('path',''))
 if binary.is_symlink() or not binary.resolve().is_relative_to(receipt.parent/'build') or not binary.is_file() or sha(binary)!=artifact.get('sha256'):
  raise ValueError('Native component artifact changed or escaped its build')
 if sha(receipt.parent/'build/compile_commands.json')!=record.get('compile_database_sha256'):
  raise ValueError('Native component compile database changed')
 binary_dependencies(binary)
 if sha(receipt)!=pin:raise ValueError('Native component receipt changed during verification')
 return dict(path=str(binary),sha256=artifact['sha256'],component_receipt=dict(path=str(receipt),sha256=pin))
