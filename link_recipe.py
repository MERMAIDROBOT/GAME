"""Recover source-only link targets from actual driver argv and pinned mappings.

Historical objects/libraries are identity references only. The output names only
new source units/targets; unrecognized inputs and reordered archive boundaries
block reconstruction rather than silently altering linkage.
"""
from pathlib import Path
import json
from clean_build import sha,ID,LINK_FLAGS,SDK_LIBRARIES,FRAMEWORKS
from compile_recipe import DRIVERS


def recover(argv,directory,mappings,name):
 if not isinstance(argv,list) or not argv or not all(isinstance(a,str) for a in argv) or Path(argv[0]).name not in DRIVERS:raise ValueError('Supply structured native linker driver argv')
 if not ID.fullmatch(name):raise ValueError('Invalid source link target id')
 directory=Path(directory).resolve();mapping_path=Path(mappings).resolve();spec=json.loads(mapping_path.read_text());by_path={}
 if spec.get('schema')!=1 or not isinstance(spec.get('products'),list):raise ValueError('Declare pinned source product mappings')
 for p in spec['products']:
  source=Path(p['input'])
  if not source.is_absolute() or source.is_symlink() or not source.is_file() or sha(source)!=p.get('sha256'):raise ValueError('Historical product differs from its mapping pin')
  if (('unit' in p)+('target' in p))!=1 or not ID.fullmatch(p.get('unit',p.get('target',''))):raise ValueError('Map each input to one source unit or target')
  if str(source.resolve()) in by_path:raise ValueError('Duplicate historical product mapping')
  by_path[str(source.resolve())]=p
 target=dict(id=name,kind='executable',units=[],uses=[],link_flags=[],frameworks=[],libraries=[],rpaths=[]);blocked=[];ignored=[];mapped=[];sdk_inputs=[];seen_archive=False;outputs=0;i=1
 while i<len(argv):
  arg=argv[i];i+=1
  try:
   if arg=='-o':
    if i>=len(argv):raise ValueError('Missing linker output')
    outputs+=1;i+=1;continue
   if arg=='-Xlinker' and argv[i:i+2]==['-cache_path_lto','-Xlinker'] and i+2<len(argv):
    ignored.append(dict(argument=argv[i+2],reason='Historical LTO cache is not reused'));i+=3;continue
   if arg in {'-dynamiclib','-shared'}:target['kind']='shared';continue
   if arg in LINK_FLAGS:target['link_flags'].append(arg);continue
   if arg=='-DNDEBUG':ignored.append(dict(argument=arg,reason='Preprocessor-only driver option with object inputs'));continue
   if arg=='-framework':
    if i>=len(argv) or argv[i] not in FRAMEWORKS:raise ValueError('Unknown SDK framework')
    target['frameworks'].append(argv[i]);i+=1;continue
   if arg in {'-install_name','-Wl,-install_name'}:
    if i>=len(argv):raise ValueError('Missing install name')
    value=argv[i];i+=1
    import re
    if not re.fullmatch(r'@rpath/[A-Za-z0-9_.-]+\.dylib',value):raise ValueError('Unsafe shared install name')
    target['install_name']=value;continue
   if arg.startswith('-Wl,-rpath,'):
    value=arg[len('-Wl,-rpath,'):]
    if value not in {'@loader_path','@executable_path/../Frameworks'}:raise ValueError('Only bundle-relative rpaths are supported')
    target['rpaths'].append(value);continue
   if arg.startswith('-l'):
    if arg[2:] not in SDK_LIBRARIES:raise ValueError('Unresolved non-SDK library: '+arg)
    target['libraries'].append(arg[2:]);continue
   if arg.startswith('-') or arg.startswith('@'):raise ValueError('Unrecognized linker argument: '+arg)
   resolved=(directory/arg).resolve();path=str(resolved);product=by_path.get(path)
   if product is None and resolved.suffix=='.tbd':
    library=Path(arg).stem.removeprefix('lib')
    if library not in SDK_LIBRARIES or Path(arg).parts[-3:-1]!=('usr','lib') or not resolved.parent.parent.parent.name.startswith('MacOSX') or not resolved.parent.parent.parent.name.endswith('.sdk') or not resolved.is_file():raise ValueError('Unrecognized SDK text stub')
    target['libraries'].append(library);sdk_inputs.append(dict(input=path,sha256=sha(resolved),library=library));continue
   if product is None:raise ValueError('Missing source mapping: '+path)
   if 'unit' in product:
    if seen_archive:raise ValueError('Direct source object after archive boundary needs explicit ordered linkage')
    target['units'].append(product['unit'])
   else:target['uses'].append(product['target']);seen_archive=True
   mapped.append(dict(input=path,sha256=product['sha256'],unit=product.get('unit'),target=product.get('target')))
  except ValueError as error:blocked.append(dict(argument=arg,error=str(error)))
 if outputs!=1:blocked.append(dict(error='Require exactly one recorded output'))
 if not target['units'] and not target['uses']:blocked.append(dict(error='No mapped source products'))
 return dict(schema=1,status='source_link_target_recovered' if not blocked else 'source_link_target_blocked',target=target if not blocked else None,blocked=blocked,mapped_inputs=mapped,ignored=ignored,mapping_sha256=sha(mapping_path),sdk_inputs=sdk_inputs,recorded_argv=argv,historical_objects_linked=False,equivalence_verified=False,gameplay_verified=False)
