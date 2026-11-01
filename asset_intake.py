"""Verified local asset staging; never downloads or modifies source assets."""
from pathlib import Path
import hashlib,json,shutil,tempfile
from clean_build import path_in,filesystem_metadata,sha


def inventory(source):
 raw=Path(source)
 if raw.is_symlink():raise ValueError('Asset source may not be a symlink')
 root=raw.resolve()
 if not root.is_dir():raise ValueError('Asset source directory missing')
 rows=[]
 for p in sorted(root.rglob('*')):
  if p.is_symlink():raise ValueError('Asset source contains a symlink')
  if p.is_file() and not filesystem_metadata(p):
   rows.append(dict(path=str(p.relative_to(root)),sha256=sha(p),bytes=p.stat().st_size))
  elif not p.is_file() and not p.is_dir():raise ValueError('Asset source contains a special file')
  if len(rows)>100000:raise ValueError('Asset inventory exceeds bound')
 if not rows:raise ValueError('Asset source is empty')
 return root,rows


def pin(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def stage(source,private,destination,expected):
 """Copy a pinned local asset set atomically, preserving failed attempts."""
 if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise ValueError('Asset intake requires a SHA256 pin')
 if not isinstance(source,(str,Path)) or not Path(source).is_absolute():raise ValueError('Asset source must be an absolute local directory')
 private=Path(private).resolve();target=path_in(private,destination)
 if target==private:raise ValueError('Asset intake destination may not be the private root')
 root,rows=inventory(source)
 if pin(rows)!=expected:raise ValueError('Asset source differs from expected pin')
 if target==root or target.is_relative_to(root) or root.is_relative_to(target):raise ValueError('Asset source and destination overlap')
 receipt=dict(schema=1,status='local_assets_staged',source=str(root),destination=str(target.relative_to(private)),sha256=expected,files=len(rows),bytes=sum(r['bytes'] for r in rows),asset_generation=False,gameplay_verified=False)
 if target.exists():
  _,actual=inventory(target)
  if actual!=rows:raise ValueError('Existing asset destination differs; preserve it for review')
  return receipt
 target.parent.mkdir(parents=True,exist_ok=True)
 attempt=Path(tempfile.mkdtemp(prefix=target.name+'-intake-',dir=target.parent))
 try:
  for row in rows:
   src=path_in(root,row['path']);dst=path_in(attempt,row['path']);dst.parent.mkdir(parents=True,exist_ok=True)
   if not src.is_file() or src.is_symlink():raise ValueError('Asset source changed during intake')
   with src.open('rb') as incoming,dst.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
   if dst.stat().st_size!=row['bytes'] or sha(dst)!=row['sha256']:raise ValueError('Copied asset does not match source manifest')
  _,copied=inventory(attempt)
  if copied!=rows:raise ValueError('Copied asset inventory differs')
  _,current=inventory(root)
  if current!=rows:raise ValueError('Asset source changed during intake')
  # Never replace an existing destination. A single caller owns this staging
  # operation via the engine compute slot; retain failed directories for review.
  if target.exists():raise ValueError('Asset destination appeared during intake')
  attempt.rename(target)
 except Exception as error:
  failure=attempt.with_name(attempt.name+'-failure.json')
  failure.write_text(json.dumps(dict(status='failed_assets_preserved',attempt=str(attempt),error=str(error)),indent=2)+'\n')
  raise
 receipts=private/'analysis/asset-intake';receipts.mkdir(parents=True,exist_ok=True)
 record=receipts/(hashlib.sha256(str(target).encode()).hexdigest()+'.json')
 record.write_text(json.dumps(receipt,indent=2)+'\n')
 return receipt


def main():
 import argparse
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--source',required=True);p.add_argument('--private',required=True);p.add_argument('--destination',required=True);p.add_argument('--sha256',required=True)
 args=p.parse_args()
 from compute_slot import slot,assert_idle
 assert_idle()
 with slot('local-asset-intake'):print(json.dumps(stage(args.source,args.private,args.destination,args.sha256),indent=2))

if __name__=='__main__':main()
