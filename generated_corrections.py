"""Replay private title corrections after each fresh translation, qualified by disc and source bytes.

This connects retained title work to the default generated-code build, which otherwise
bypasses source-graph correction replay. Records are pinned data; no shell or Python hooks.
"""
from pathlib import Path
import hashlib,json,re,os,tempfile
from clean_build import path_in,sha


def retain_baselines(private, baselines):
 """Keep exact pre-correction bytes for future compiler updates, indexed by SHA."""
 private=Path(private).resolve();records=[]
 # Validate every cache member before creating any new one.
 prepared=[]
 for pin,raw in baselines.items():
  if not re.fullmatch(r'[a-f0-9]{64}',pin) or len(raw)>16*1024*1024 or hashlib.sha256(raw).hexdigest()!=pin:
   raise ValueError('Invalid pristine correction baseline')
  target=path_in(private,'analysis/generated-correction-baselines/'+pin+'.cpp')
  if target.exists() and sha(target)!=pin:raise ValueError('Retained correction baseline pin changed')
  prepared.append((pin,raw,target))
 for pin,raw,target in prepared:
  if not target.exists():
   target.parent.mkdir(parents=True,exist_ok=True)
   fd,name=tempfile.mkstemp(prefix='.baseline-',suffix='.tmp',dir=target.parent);temporary=Path(name)
   try:
    with os.fdopen(fd,'wb') as stream:stream.write(raw)
    if target.exists():
     if target.is_symlink() or sha(target)!=pin:raise ValueError('Retained correction baseline changed during capture')
    else:temporary.replace(target)
   finally:temporary.unlink(missing_ok=True)
  records.append(dict(path=str(target.relative_to(private)),sha256=pin))
 return records


def apply(profile,private):
 spec=profile.get('generated_source_corrections')
 if spec is None:return None
 private=Path(private).resolve();root=private/'generated'
 if not isinstance(spec,dict) or set(spec)!={'path','sha256'}:raise ValueError('Invalid generated correction declaration')
 manifest=path_in(private,spec['path'])
 if sha(manifest)!=spec['sha256']:raise ValueError('Generated correction manifest pin changed')
 record=json.loads(manifest.read_text())
 if set(record)!={'schema','kind','executable_sha256','edits','headers'} or record['schema']!=1 or record['kind']!='revision_generated_corrections':raise ValueError('Unknown generated correction schema')
 import pipeline as lab
 elf=lab.boot_executable(private/'disc-root',profile)
 if sha(elf)!=record['executable_sha256'] or sha(elf)!=profile.get('expected_sha256'):raise ValueError('Generated corrections require the exact executable revision')
 if root.is_symlink() or not root.is_dir():raise ValueError('Generated correction root missing or unsafe')
 edits=record['edits'];headers=record['headers']
 if not isinstance(edits,list) or not edits or len(edits)>256 or not isinstance(headers,list) or len(headers)>64:raise ValueError('Invalid generated correction inventory')
 pending=[];seen=set();observed=[];baselines={}
 for row,header in [(r,False) for r in edits]+[(r,True) for r in headers]:
  fields={'file','source','after_sha256','reason'} | (set() if header else {'before_sha256'})
  if not isinstance(row,dict) or set(row)!=fields or not isinstance(row['reason'],str) or not row['reason'].strip():raise ValueError('Invalid generated source correction')
  name=row['file']
  if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.'+('(?:h|hpp)' if header else 'cpp'),name) or name in seen:raise ValueError('Invalid or duplicate correction destination')
  seen.add(name);target=root/name
  if target.is_symlink():raise ValueError('Generated correction destination is a symlink')
  replacement=path_in(private,row['source'])
  if not replacement.is_file() or replacement.stat().st_size>16*1024*1024:raise ValueError('Generated correction source missing or too large')
  raw=replacement.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=row['after_sha256']:raise ValueError('Generated replacement pin changed')
  current=target.read_bytes() if target.is_file() else None
  digest=hashlib.sha256(current).hexdigest() if current is not None else None
  if digest!=row['after_sha256'] and ((header and current is not None) or (not header and digest!=row['before_sha256'])):raise ValueError('Generated source identity drift: '+name)
  if not header and digest==row['before_sha256']:baselines[digest]=current
  if digest!=row['after_sha256']:pending.append((target,current,raw))
  observed.append(dict(file=name,sha256=row['after_sha256'],reason=row['reason']))
 retained=retain_baselines(private,baselines)
 # Validate every file before mutating any. An interrupted batch can be retried from either pinned state.
 for target,current,raw in pending:
  if (target.read_bytes() if target.exists() else None)!=current:raise ValueError('Generated source changed during correction replay')
  fd,name=tempfile.mkstemp(prefix='.correction-',suffix='.tmp',dir=root);temporary=Path(name)
  try:
   with os.fdopen(fd,'wb') as f:f.write(raw)
   temporary.replace(target)
  finally:temporary.unlink(missing_ok=True)
 result=dict(schema=1,status='revision_generated_corrections_applied',manifest_sha256=spec['sha256'],executable_sha256=record['executable_sha256'],files=observed,changed=[p.name for p,_,_ in pending],baselines_retained=retained,gameplay_verified=False)
 lab.save(private/'analysis/generated-correction-stage.json',result)
 return result


def verify(profile,private,receipt):
 """Packaging must not silently use a pre-correction runner with a newer title profile."""
 spec=profile.get('generated_source_corrections')
 if spec is None:
  if receipt:raise ValueError('Build contains undeclared generated corrections')
  return
 private=Path(private).resolve();manifest=path_in(private,spec['path'])
 if sha(manifest)!=spec['sha256'] or not receipt or receipt.get('manifest_sha256')!=spec['sha256']:raise ValueError('Build does not include current generated corrections')
 record=json.loads(manifest.read_text())
 import pipeline as lab
 actual=sha(lab.boot_executable(private/'disc-root',profile))
 if actual!=record['executable_sha256'] or actual!=profile.get('expected_sha256'):raise ValueError('Generated correction executable revision changed after build')
 expected={r['file']:r['after_sha256'] for r in record['edits']+record['headers']}
 if {r['file']:r['sha256'] for r in receipt.get('files',[])}!=expected:raise ValueError('Generated correction build coverage differs')
 for name,pin in expected.items():
  target=private/'generated'/name
  if target.is_symlink() or sha(target)!=pin:raise ValueError('Generated correction source changed after build')
