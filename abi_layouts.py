"""Check compiler-derived shared record layouts across source components.

A layout match is an ABI boundary check, not game or whole-program equivalence.
Only fixed syntax-only compiler operations run; supplied shell commands cannot run.
"""
from compute_slot import exclusive
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess
from clean_build import inspect,sha,classify_dependencies,compiler_header_roots,ID
from dependencies import dependency_paths

NAME=re.compile(r'[A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*\Z')
HEADER=re.compile(r'[A-Za-z_0-9./-]+\.(?:h|hpp|hxx|hh)\Z')
TYPE=re.compile(r'(?:const )?(?:unsigned |signed )?[A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*(?: const)?(?:\s*[*&](?: const)?)*\Z')


def probe_source(entry,headers):
 if any(not isinstance(h,str) or not HEADER.fullmatch(h) or '..' in Path(h).parts for h in headers):raise ValueError('Declare safe source header names')
 name=entry.get('record',entry.get('function',''))
 if not isinstance(name,str) or not NAME.fullmatch(name):raise ValueError('Declare a safe shared ABI name')
 prefix=''.join('#include "'+h+'"\n' for h in headers)
 if 'record' in entry:return prefix+'static_assert(sizeof(::'+entry['record']+') > 0);\n'
 result=entry.get('returns','');parameters=entry.get('parameters')
 if not isinstance(result,str) or not TYPE.fullmatch(result) or not isinstance(parameters,list) or any(not isinstance(t,str) or not TYPE.fullmatch(t) or t=='void' for t in parameters):raise ValueError('Declare safe function return and parameter types')
 if type(entry.get('noexcept',False)) is not bool:raise ValueError('Function noexcept must be boolean')
 pointer=result+' (*)('+', '.join(parameters)+')'+(' noexcept' if entry.get('noexcept') else '')
 return prefix+'#include <type_traits>\nusing ExpectedSignature = '+pointer+';\nstatic_assert(std::is_same_v<decltype(&::'+entry['function']+'), ExpectedSignature>, "Shared function signature differs");\n'


def parse_layout(output,record):
 pattern=re.compile(r'^\s*0\s*\| (?:struct|class|union) '+re.escape(record)+r'\s*$')
 layouts=[];active=None
 for line in output.splitlines():
  if pattern.fullmatch(line):active=[]
  if active is not None:
   active.append(line.strip())
   if ']' in line and 'nvalign=' in line:
    layouts.append('\n'.join(active));active=None
 if not layouts or len(set(layouts))!=1:raise ValueError('Missing or conflicting compiler layout for '+record)
 return layouts[0]


@exclusive('abi-layouts')
def audit(config_path,destination,*,slot_check=True):
 config_path=Path(config_path).resolve();config_bytes=config_path.read_bytes();config=json.loads(config_bytes);out=Path(destination).resolve()
 records=config.get('records',[]);functions=config.get('functions',[])
 if config.get('schema')!=1 or not isinstance(records,list) or not isinstance(functions,list) or not (records or functions):raise ValueError('Declare shared ABI records/functions and source probes')
 if out.exists():raise ValueError('Preserve prior ABI receipts')
 checks=[];ids=set();entry_names=set()
 for entry in records+functions:
  record=entry.get('record',entry.get('function',''));probes=entry.get('probes')
  if ('record' in entry)==('function' in entry):raise ValueError('Each ABI entry must declare one record or function')
  key=('function' if 'function' in entry else 'record',record)
  if key in entry_names:raise ValueError('Duplicate shared ABI entry')
  entry_names.add(key)
  if not NAME.fullmatch(record) or not isinstance(probes,list) or len(probes)<2:raise ValueError('Each record requires two independent source probes')
  sources=set()
  for probe in probes:
   name=probe.get('id','')
   if not ID.fullmatch(name) or name in ids:raise ValueError('Invalid or duplicate ABI probe id')
   ids.add(name);checked=inspect(probe['recipe']);checked['input_path']=str(Path(probe['recipe']).resolve());unit=next((u for u in checked['recipe']['units'] if u['id']==probe.get('unit')),None)
   if checked['recipe_sha256']!=probe.get('recipe_sha256') or unit is None:raise ValueError('ABI probe recipe or unit differs from pin')
   if Path(unit['source']).suffix not in {'.cc','.cpp','.cxx','.mm'}:raise ValueError('ABI probes require C++ source settings')
   headers=probe.get('headers')
   if not isinstance(headers,list) or not headers or any(not isinstance(h,str) or not HEADER.fullmatch(h) or '..' in Path(h).parts for h in headers):raise ValueError('Declare safe source header names')
   source_text=probe_source(entry,headers)
   sources.add((str(checked['root']),unit['id']));checks.append((record,name,headers,checked,unit,source_text,'function' in entry))
  if len(sources)<2:raise ValueError('ABI probes must use independent source units')
 compiler=shutil.which('clang++')
 if not compiler:raise ValueError('ABI layout checks require Clang')
 if slot_check:
  processes=subprocess.check_output(['/bin/ps','-axo','comm='],text=True)
  if any(Path(p.strip()).name in {'ps2EntryRunner','ninja','clang','clang++','blender','realesrgan-ncnn-vulkan'} for p in processes.splitlines()):raise RuntimeError('Game/build/render slot is occupied')
 env={k:v for k,v in os.environ.items() if k not in {'CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','DYLD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES','SDKROOT','MACOSX_DEPLOYMENT_TARGET','COMPILER_PATH','GCC_EXEC_PREFIX','CCC_OVERRIDE_OPTIONS'} and not k.startswith(('PS2_PORT_','PS2X_'))}
 roots=compiler_header_roots(compiler,'c++',env);out.mkdir(parents=True)
 report=dict(schema=1,status='running',config_sha256=hashlib.sha256(config_bytes).hexdigest(),compiler=str(Path(compiler).resolve()),compiler_sha256=sha(Path(compiler).resolve()),probes=[],records=[],functions=[],equivalence_verified=False,gameplay_verified=False)
 def save():(out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 save()
 try:
  for record,name,headers,checked,unit,source_text,function in checks:
   root=checked['root'];source=out/(name+'.cpp');dep=out/(name+'.d');log=out/(name+'.log')
   source.write_text(source_text)
   args=[compiler,'-std='+unit.get('standard','c++20'),'-fsyntax-only','-Xclang','-fdump-record-layouts','-MD','-MF',str(dep),*unit.get('flags',[])]
   args+=['-D'+x for x in unit.get('defines',[])];args+=['-I'+str(root/x) for x in unit.get('includes',[])]
   for inc in unit.get('include_paths',[]):args.extend([inc['kind'],str(root/inc['path'])])
   if unit.get('sdk_framework_search'):
    sdk=subprocess.check_output(['/usr/bin/xcrun','--show-sdk-path'],text=True,env=env).strip();args+=['-F'+str(Path(sdk)/'System/Library/Frameworks')]
   for h in unit.get('force_include',[]):args+=['-include',str(root/h)]
   args.append(str(source))
   with log.open('w') as f:subprocess.run(args,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
   deps=[p for p in dependency_paths(dep.read_text(),root) if Path(p).resolve()!=source.resolve()]
   project,toolchain=classify_dependencies(deps,root,checked['recipe']['files'],roots)
   layout=source_text.split('using ExpectedSignature = ',1)[1].split(';',1)[0] if function else parse_layout(log.read_text(),record)
   if inspect(checked['input_path'])['recipe_sha256']!=checked['recipe_sha256']:raise ValueError('ABI source recipe changed during compilation')
   report['probes'].append(dict(id=name,record=record,kind='function' if function else 'record',recipe_sha256=checked['recipe_sha256'],unit=unit['id'],probe_sha256=sha(source),command=args,project_dependencies=project,toolchain_headers=toolchain,layout=layout,layout_sha256=hashlib.sha256(layout.encode()).hexdigest()));save()
  for entry in records:
   probes=[p for p in report['probes'] if p['record']==entry['record']];report['records'].append(dict(record=entry['record'],matches=len({p['layout_sha256'] for p in probes})==1,probes=[p['id'] for p in probes]))
  for entry in functions:
   probes=[p for p in report['probes'] if p['record']==entry['function'] and p['kind']=='function'];report['functions'].append(dict(function=entry['function'],matches=len(probes)==len(entry['probes']),probes=[p['id'] for p in probes]))
  if sha(config_path)!=report['config_sha256']:raise ValueError('ABI configuration changed')
  report['status']='abi_layouts_match' if all(r['matches'] for r in report['records']+report['functions']) else 'abi_layouts_mismatch';save();return report
 except Exception as error:
  report.update(status='failed',error=str(error));save();raise
