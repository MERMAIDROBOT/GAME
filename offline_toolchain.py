"""Reconstruct pinned public tool sources from local Git objects, offline.

Working-tree edits and historical tool binaries are never copied. Existing
checkouts remain untouched; this produces independent detached checkouts.
"""
from pathlib import Path
import json,re,subprocess
from compute_slot import exclusive,assert_idle
from tool_state import tree_pin
from clean_build import sha,ID,filesystem_metadata


def clean_pack_metadata(target):
 """Discard only recognizable macOS metadata in a newly staged Git pack dir."""
 target=Path(target).resolve();pack=target/'.git/objects/pack';removed=[]
 if not pack.exists():return removed
 if pack.is_symlink() or not pack.resolve().is_relative_to(target):raise ValueError('Unsafe staged Git pack directory')
 for path in sorted(pack.glob('._pack-*')):
  sibling=path.with_name(path.name[2:])
  if path.is_symlink() or sibling.is_symlink():raise ValueError('Unsafe staged Git pack metadata')
  if not sibling.is_file() or sibling.suffix not in {'.idx','.pack','.rev'}:continue
  with path.open('rb') as stream:header=stream.read(8)
  if header!=bytes.fromhex('0005160700020000'):continue
  removed.append(dict(path=str(path.relative_to(target)),sha256=sha(path)))
  path.unlink()
 return removed


def clean_checkout_metadata(target):
 """Clean generated metadata only in a fresh engine-owned checkout."""
 target=Path(target).resolve();removed=clean_pack_metadata(target)
 tracked=set(subprocess.check_output(['git','-C',str(target),'ls-files','-z'],text=True).split('\0'))
 for path in sorted(target.rglob('._*')):
  name=str(path.relative_to(target))
  if path.is_file() and name not in tracked and filesystem_metadata(path):
   removed.append(dict(path=name,sha256=sha(path)));path.unlink()
 return removed


def clean_isolated_metadata(lab):
 """Recheck isolated ownership before cleaning after patch-created files."""
 receipt=Path(lab.WORK)/'offline-source-stage.json'
 if not receipt.is_file():return None
 report=json.loads(receipt.read_text())
 expected={Path(lab.SOURCE).resolve(),*((Path(lab.BUILD)/'_deps'/(name+'-src')).resolve() for name in lab.LOCK.get('resolved_dependencies',{}))}
 actual={Path(row['path']).resolve() for row in report.get('sources',[])}
 if report.get('status')!='locked_public_sources_staged_not_built' or report.get('lock_sha256')!=sha(lab.ROOT/'toolchain.lock.json') or actual!=expected:
  raise ValueError('Isolated source ownership receipt changed')
 if any(not root.is_relative_to(Path(lab.WORK).resolve()) for root in actual):raise ValueError('Isolated source escaped workspace')
 removed={str(root):clean_checkout_metadata(root) for root in sorted(actual)}
 result=dict(schema=1,source_stage_sha256=sha(receipt),removed=removed)
 (Path(lab.WORK)/'metadata-cleanup.json').write_text(json.dumps(result,indent=2)+'\n')
 return result


@exclusive('offline-toolchain-stage')
def stage(lab,destination):
 assert_idle();out=Path(destination).resolve()
 if out.exists():raise ValueError('Use a fresh isolated tool workspace')
 for source in [lab.ROOT,lab.SOURCE,lab.BUILD,lab.ENV]:
  source=Path(source).resolve()
  if out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('Tool workspace overlaps existing inputs')
 entries=[dict(name='PS2Recomp',source=Path(lab.SOURCE).resolve(),revision=lab.LOCK['ps2recomp']['revision'],destination='dependencies/PS2Recomp')]
 for name,revision in lab.LOCK.get('resolved_dependencies',{}).items():
  if not isinstance(name,str) or not ID.fullmatch(name):raise ValueError('Invalid locked dependency name')
  entries.append(dict(name=name,source=(lab.BUILD/'_deps'/(name+'-src')).resolve(),revision=revision,destination='build/ps2recomp/_deps/'+name+'-src'))
 for row in entries:
  if not isinstance(row['revision'],str) or not re.fullmatch('[a-f0-9]{40}',row['revision']):raise ValueError('Invalid pinned source revision')
  if not row['source'].is_dir():raise ValueError('Missing local public source repository: '+row['name'])
  kind=subprocess.check_output(['git','-C',str(row['source']),'cat-file','-t',row['revision']],text=True).strip()
  if kind!='commit':raise ValueError('Pinned local revision is not a commit')
 out.mkdir(parents=True);report=dict(schema=1,status='staging',lock_sha256=sha(lab.ROOT/'toolchain.lock.json'),sources=[],network_used=False,historical_binaries_copied=False,source_checkouts_modified=False)
 def save():(out/'offline-source-stage.json').write_text(json.dumps(report,indent=2)+'\n')
 save()
 try:
  for row in entries:
   target=out/row['destination'];target.parent.mkdir(parents=True,exist_ok=True)
   # Absolute local pack transport also supports shallow repositories.
   # exFAT may still attach AppleDouble metadata to newly created packs.
   subprocess.run(['git','clone','--no-local','--no-hardlinks','--no-checkout',str(row['source']),str(target)],check=True)
   metadata=clean_pack_metadata(target)
   subprocess.run(['git','-C',str(target),'checkout','--detach',row['revision']],check=True)
   metadata+=clean_checkout_metadata(target)
   subprocess.run(['git','-C',str(target),'fsck','--connectivity-only','--no-dangling'],check=True)
   actual=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
   if actual!=row['revision']:raise ValueError('Isolated tool source revision differs')
   report['sources'].append(dict(name=row['name'],revision=actual,path=str(target),source_sha256=tree_pin(target),local_object_source=str(row['source']),discarded_pack_metadata=metadata));save()
  if sha(lab.ROOT/'toolchain.lock.json')!=report['lock_sha256']:raise ValueError('Toolchain lock changed during isolation')
  report.update(status='locked_public_sources_staged_not_built',toolchain=str(Path(lab.ENV).resolve()));save();return report
 except Exception as error:
  report.update(status='failed',error=str(error));save();raise
