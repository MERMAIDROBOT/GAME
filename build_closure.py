"""Classify real native link inputs against compiler outputs; never execute a link recipe.

Exact paths only: matching a unity object's basename is not proof of source closure.
Archives remain blockers until the source-only builder produces them in this graph.
"""
from pathlib import Path
import json,shlex,re
from clean_build import sha,compiled_binary


def adjacent_source_candidates(path):
 p=Path(path);name=re.sub(r'\.(?:o|obj|bc)(?:\.(?:v[0-9]+|old|backup))?$','',p.name)
 if name==p.name:return []
 result=[]
 for source in [p.parent/name,p.parent.parent/name]:
  if source.suffix not in {'.c','.cc','.cpp','.cxx','.m','.mm','.S'} or source.is_symlink() or not source.is_file():continue
  row=dict(source=str(source.resolve()),source_sha256=sha(source),relationship='adjacent_source_candidate_not_equivalence')
  if row not in result:result.append(row)
 return result


def compiled_input(path):
 p=Path(path)
 if p.suffix.lower() in {'.o','.a','.dylib','.so','.bc','.obj','.lib'}:return True
 return compiled_binary(p)


def audit(command, compile_database, directory):
 if not isinstance(command,list) or not all(isinstance(a,str) for a in command):
  raise ValueError('Supply the actual structured linker argv, not a wrapper command or shell')
 directory=Path(directory).resolve()
 if not command or Path(command[0]).name not in {'clang++','clang','c++','g++','ld','ld64'}:
  raise ValueError('Expected native linker/compiler driver argv')
 def full(p,base=directory):return (base/Path(p)).resolve()
 db=json.loads(Path(compile_database).read_text());outputs={}
 for entry in db:
  base=Path(entry['directory']).resolve();args=entry.get('arguments') or shlex.split(entry['command'])
  if '-c' not in args or args.count('-o')!=1:continue
  obj=full(args[args.index('-o')+1],base);src=full(entry['file'],base)
  if obj in outputs and outputs[obj]!=src:raise ValueError('Ambiguous compile database output: '+str(obj))
  outputs[obj]=src
 records=[];skip=False
 for arg in command[1:]:
  if skip:skip=False;continue
  if arg in {'-o','-framework','-install_name','-rpath','-isysroot','-syslibroot','-target','-arch'}:skip=True;continue
  if arg.startswith('@') or arg.startswith('-Wl,@'):raise ValueError('Expand response files into structured arguments before auditing')
  if arg.startswith('-l'):
   records.append(dict(input=arg,status='unresolved_library',action='resolve_library_to_sdk_or_pinned_source'));continue
  suffix=Path(arg).suffix.lower()
  p=full(arg)
  if not compiled_input(p):continue
  src=outputs.get(p)
  if src and src.is_file():
   records.append(dict(input=str(p),status='source_mapped_not_built',source=str(src),source_sha256=sha(src),action='stage_compiler_dependencies_and_rebuild'))
  else:
   records.append(dict(input=str(p),status='historical_compiled_input',action='replace_with_source_target',source_mapping_available=bool(src),source_candidates=adjacent_source_candidates(p)))
 if not records:raise ValueError('No link inputs found')
 return dict(schema=1,compile_database_sha256=sha(compile_database),clean_rebuild_ready=False,gameplay_verified=False,
  inputs=records,source_mapped=sum(r['status']=='source_mapped_not_built' for r in records),
  blockers=sum(r['status']!='source_mapped_not_built' for r in records),
  next_action='stage_and_build_source_graph' if all(r['status']=='source_mapped_not_built' for r in records) else 'resolve_historical_inputs')
