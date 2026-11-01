"""Reconstruct a clean disc-file input from a pinned preserved-copy manifest.

Extra saves/test files are left at the source. No raw image is required or made.
This verifies retained file bytes, not an independent retail-dump identity.
"""
from pathlib import Path
import json,re,shutil,tempfile
from clean_build import path_in,sha
from compute_slot import exclusive,assert_idle


@exclusive('preserved-disc-intake')
def import_copy(source,manifest,expected,profile,private):
 import pipeline as lab
 source_path=Path(source);manifest=Path(manifest)
 if source_path.is_symlink() or manifest.is_symlink():raise ValueError('Preserved input must not be a symlink')
 if Path(private).is_symlink():raise ValueError('Private destination may not be a symlink')
 source=source_path.resolve();private=Path(private).resolve();manifest=manifest.resolve()
 if not source.is_dir() or not manifest.is_file() or manifest.stat().st_size>32*1024*1024:raise ValueError('Missing or oversized preserved input')
 if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected) or sha(manifest)!=expected:raise ValueError('Preserved manifest pin mismatch')
 data=json.loads(manifest.read_text());rows=data.get('files');seen=set()
 if not isinstance(rows,list) or not 1<=len(rows)<=100000 or data.get('complete') is False:raise ValueError('Preserved manifest is empty or incomplete')
 if data.get('expected_file_count',len(rows))!=len(rows):raise ValueError('Preserved manifest count mismatch')
 for row in rows:
  if not isinstance(row,dict) or row.get('status')!='copied' or not isinstance(row.get('path'),str):raise ValueError('Invalid preserved file record')
  rel=str(lab.safe_relative(row['path']))
  if rel in seen or type(row.get('bytes')) is not int or row['bytes']<0 or not isinstance(row.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError('Invalid or repeated preserved file identity')
  path=path_in(source,rel)
  if not path.is_file() or path.stat().st_size!=row['bytes']:raise ValueError('Preserved file missing or changed size: '+rel)
  seen.add(rel)
 if data.get('total_bytes',sum(r['bytes'] for r in rows))!=sum(r['bytes'] for r in rows):raise ValueError('Preserved byte count mismatch')
 target=path_in(private,'disc-root');report_path=path_in(private,'analysis/preserved-disc-import.json')
 if target.exists() or target.is_symlink() or (private/'disc-manifest.json').exists() or report_path.exists():raise ValueError('Use fresh private storage; existing disc/saves/evidence are preserved')
 if private==source or private.is_relative_to(source) or source.is_relative_to(private):raise ValueError('Preserved source and destination overlap')
 elf=lab.boot_executable(source,profile);identity={'executable':str(elf.relative_to(source)),'sha256':sha(elf)}
 if identity['executable'] not in seen:raise ValueError('Boot executable is outside preserved manifest')
 if profile.get('expected_sha256') and identity['sha256']!=profile['expected_sha256']:raise ValueError('Preserved executable differs from profile')
 # No compiler, game, GPU or render may compete with this multi-GiB intake.
 assert_idle()
 private.mkdir(parents=True,exist_ok=True)
 attempt=Path(tempfile.mkdtemp(prefix='disc-intake-',dir=private));records=[]
 report=dict(schema=1,status='copying',source=str(source),manifest=str(manifest),manifest_sha256=expected,
  attempt=str(attempt),selected_files=len(rows),selected_bytes=sum(r['bytes'] for r in rows),copied_files=0,
  source_modified=False,saves_migrated=False,raw_sector_image=False,gameplay_verified=False)
 lab.save(report_path,report)
 try:
  for row in rows:
   original=path_in(source,row['path']);out=path_in(attempt,row['path']);out.parent.mkdir(parents=True,exist_ok=True)
   with original.open('rb') as incoming,out.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
   if out.stat().st_size!=row['bytes'] or sha(out)!=row['sha256']:raise ValueError('Preserved bytes differ: '+row['path'])
   records.append(dict(path=row['path'],bytes=row['bytes'],sha256=row['sha256'],status='copied'))
   report['copied_files']=len(records)
   if len(records)%32==0:lab.save(report_path,report)
  if sha(manifest)!=expected:raise ValueError('Preserved manifest changed during intake')
  copied_elf=lab.boot_executable(attempt,profile)
  if str(copied_elf.relative_to(attempt))!=identity['executable'] or sha(copied_elf)!=identity['sha256']:raise ValueError('Copied boot identity differs')
  if target.exists():raise ValueError('Disc destination appeared during intake')
  attempt.rename(target)
  lab.save(private/'disc-manifest.json',dict(source=str(source),source_kind='verified_preserved_file_copy',
   identity=identity,complete=True,expected_file_count=len(records),total_bytes=sum(r['bytes'] for r in records),
   preserved_manifest_sha256=expected,raw_sector_image=False,files=records))
  report.update(status='preserved_files_imported',disc_root=str(target),disc_manifest_sha256=sha(private/'disc-manifest.json'),identity=identity)
  lab.save(report_path,report)
  return report
 except Exception as error:
  report.update(status='failed_attempt_preserved',error=str(error));lab.save(report_path,report);raise
