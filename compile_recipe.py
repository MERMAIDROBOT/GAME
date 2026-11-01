"""Translate recorded compiler argv to declarative source settings without execution.

Unknown options fail closed. No command substitutions, response files, compiler
plugins, historical PCHs, or link inputs enter a source build recipe.
"""
from pathlib import Path
import hashlib,json,re,shlex
from clean_build import FLAGS,STANDARDS

DRIVERS={'cc','c++','clang','clang++','gcc','g++'}
WARNINGS={'-Wall','-Wextra','-Wpedantic','-Werror=implicit-function-declaration','-Werror=pointer-arith','-Wno-unused-command-line-argument','-Wno-deprecated-declarations','-Winvalid-pch'}
DEFINE=re.compile(r'[A-Za-z_][A-Za-z_0-9]*(?:=[A-Za-z0-9_./:+-]+)?\Z')


def settings(entry,*,source_pch=False):
 if not isinstance(entry,dict):raise ValueError('Expected a compile database entry')
 cwd=Path(entry.get('directory',''))
 if not cwd.is_absolute():raise ValueError('Compiler directory must be absolute')
 raw=entry.get('arguments',entry.get('command'))
 args=shlex.split(raw) if isinstance(raw,str) else raw
 if not isinstance(args,list) or not args or not all(isinstance(a,str) for a in args):raise ValueError('Expected compiler argv')
 if Path(args[0]).name not in DRIVERS:raise ValueError('Unknown compiler driver')
 if args.count('-c')!=1 or args.count('-o')!=1:raise ValueError('Require exactly one compile-only source/output')
 args=list(args)
 # Compiler -c is a mode flag; Make commonly puts the sole source last.
 ci=args.index('-c')
 if ci+1<len(args) and args[ci+1].startswith('-'):
  last=args[-1]
  if last.startswith('-') or Path(last).suffix not in {'.c','.cc','.cpp','.cxx','.m','.mm','.S'}:raise ValueError('Missing compile source')
  args.pop();args.insert(ci+1,last)
 def absolute(value):return str((cwd/value).resolve())
 result=dict(source=None,output=None,flags=[],defines=[],include_paths=[],force_include=[])
 i=1
 while i<len(args):
  token=args[i];i+=1
  if token=='-Xclang' and source_pch:
   # CMake records both the cache and its original forced source header. Accept
   # only that exact pattern; arbitrary frontend options remain forbidden.
   tail=args[i-1:i+7]
   if len(tail)!=8 or tail[:3]!=['-Xclang','-include-pch','-Xclang'] or tail[4:7]!=['-Xclang','-include','-Xclang']:
    raise ValueError('Unsupported frontend/PCH reconstruction pattern')
   cache,header=Path(absolute(tail[3])),Path(absolute(tail[7]))
   if str(cache)!=str(header)+'.pch' or header.suffix not in {'.h','.hh','.hpp','.hxx'} or not header.is_file() or header.is_symlink():
    raise ValueError('PCH must have its matching real source header')
   result['force_include'].append(str(header))
   result.setdefault('pch_reconstructed',[]).append(dict(header=str(header),sha256=hashlib.sha256(header.read_bytes()).hexdigest(),historical_cache_used=False))
   i+=7;continue
  if token in {'-c','-o','-I','-isystem','-iquote','-include','-std','-D','-F'}:
   if i==len(args):raise ValueError('Missing option value: '+token)
   value=args[i];i+=1
  elif token.startswith(('-I','-D','-F')) and len(token)>2:token,value=token[:2],token[2:]
  elif token.startswith('-std='):token,value='-std',token[5:]
  elif token in FLAGS or token in WARNINGS:result['flags'].append(token);continue
  else:raise ValueError('Unsupported compiler argument: '+token)
  if token=='-c':result['source']=absolute(value)
  elif token=='-o':result['output']=absolute(value)
  elif token=='-std':
   if value not in STANDARDS:raise ValueError('Unsupported compiler standard')
   result['standard']=value
  elif token=='-D':
   if not DEFINE.fullmatch(value):raise ValueError('Unsupported compiler define')
   result['defines'].append(value)
  elif token in {'-I','-isystem','-iquote'}:result['include_paths'].append(dict(kind=token,path=absolute(value)))
  elif token=='-include':result['force_include'].append(absolute(value))
  elif token=='-F':
   # Apple SDK framework search is reconstructed from the active toolchain SDK.
   # Never accept an arbitrary framework directory as an implicit binary input.
   p=Path(absolute(value))
   if p.parts[-3:]!=('System','Library','Frameworks') or not p.parent.parent.parent.name.endswith('.sdk'):
    raise ValueError('Only SDK framework search paths are supported')
   result['sdk_framework_search']=True
 if entry.get('file') and absolute(entry['file'])!=result['source']:raise ValueError('Compile database source disagrees with argv')
 if entry.get('output') and absolute(entry['output'])!=result['output']:raise ValueError('Compile database output disagrees with argv')
 if Path(result['source']).suffix not in {'.c','.cc','.cpp','.cxx','.m','.mm','.S'}:raise ValueError('Not a source compile unit')
 if 'standard' not in result:result['standard']='gnu11' if Path(result['source']).suffix in {'.c','.m'} else 'gnu++17'
 return result


def recover(database,*,source_pch=False):
 p=Path(database).resolve();raw=p.read_bytes();entries=json.loads(raw)
 if not isinstance(entries,list):raise ValueError('Compile database must be an array')
 accepted=[];blocked=[]
 for ordinal,entry in enumerate(entries):
  try:accepted.append(dict(ordinal=ordinal,**settings(entry,source_pch=source_pch)))
  except (ValueError,TypeError) as error:blocked.append(dict(ordinal=ordinal,source=entry.get('file') if isinstance(entry,dict) else None,error=str(error)))
 return dict(schema=1,database=str(p),database_sha256=hashlib.sha256(raw).hexdigest(),
  accepted=accepted,blocked=blocked,recorded_settings_recovered=not blocked,
  source_equivalence_verified=False,dependency_closure_verified=False,gameplay_verified=False,
  source_pch_reconstruction_requested=source_pch)
