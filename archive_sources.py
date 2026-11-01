"""Read static archive members and locate exact-byte recorded compiler outputs.

An object/source candidate is not source equivalence; rebuild controls are still
required. Thin archives are rejected because they depend on external objects.
"""
from pathlib import Path
import hashlib,json,shlex
from clean_build import sha


def members(path):
 out=[];strings=b''
 with Path(path).open('rb') as f:
  if f.read(8)!=b'!<arch>\n':raise ValueError('Expected self-contained Unix static archive (thin archives rejected)')
  while True:
   header=f.read(60)
   if not header:break
   if len(header)!=60 or header[58:]!=b'`\n':raise ValueError('Malformed archive member header')
   try:size=int(header[48:58].strip());name=header[:16].decode('ascii').strip()
   except (ValueError,UnicodeError):raise ValueError('Invalid archive size/name') from None
   if size<0 or size>512*1024*1024:raise ValueError('Archive member exceeds bound')
   data=f.read(size)
   if len(data)!=size:raise ValueError('Truncated archive member')
   if size%2 and f.read(1)!=b'\n':raise ValueError('Invalid archive padding')
   if name=='//':strings=data;continue
   if name.startswith('#1/'):
    try:n=int(name[3:])
    except ValueError:raise ValueError('Invalid BSD extended name') from None
    if n<1 or n>len(data):raise ValueError('Invalid BSD name length')
    name=data[:n].split(b'\0',1)[0].decode('utf-8');data=data[n:]
   elif name.startswith('/') and name[1:].isdigit():
    offset=int(name[1:])
    if offset>=len(strings):raise ValueError('Invalid GNU long-name offset')
    name=strings[offset:].split(b'\n',1)[0].decode('utf-8').rstrip('/')
   else:name=name.rstrip('/')
   if not name or name in {'/','/SYM64'} or name.startswith('__.SYMDEF'):continue
   if '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unexpected archive member path')
   out.append(dict(name=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),ordinal=len(out)))
   if len(out)>100000:raise ValueError('Too many archive members')
 return out


def compiler_records(paths):
 records=[]
 for path in paths:
  p=Path(path).resolve();data=json.loads(p.read_text())
  entries=data if isinstance(data,list) and (not data or isinstance(data[0],dict)) else [data]
  for entry in entries:
   if isinstance(entry,dict):
    args=entry.get('arguments') or entry.get('cmd') or entry.get('command')
    cwd=Path(entry.get('directory',entry.get('cwd',p.parent))).resolve()
   else:args=entry;cwd=p.parent
   if isinstance(args,str):args=shlex.split(args)
   if not isinstance(args,list) or not all(isinstance(a,str) for a in args):continue
   if args.count('-c')!=1 or args.count('-o')!=1:continue
   source=(cwd/args[args.index('-c')+1]).resolve();obj=(cwd/args[args.index('-o')+1]).resolve()
   if not source.is_file() or not obj.is_file():continue
   records.append(dict(object=str(obj),source=str(source),record=str(p)))
 return records


def audit(archive,records):
 inventory=members(archive);by_name={};cache={}
 for r in records:by_name.setdefault(r.get('archive_member',Path(r['object']).name),[]).append(r)
 for item in inventory:
  exact=[]
  for candidate in by_name.get(item['name'],[]):
   p=Path(candidate['object'])
   if str(p) not in cache:cache[str(p)]=sha(p)
   if cache[str(p)]==item['sha256']:
    exact.append(dict(candidate,source_sha256=sha(candidate['source'])))
  item.update(status='recorded_object_match_source_unverified' if exact else 'no_recorded_object_match',source_candidates=exact,source_hash_conflict=len({r['source_sha256'] for r in exact})>1)
 return dict(schema=1,archive=str(Path(archive).resolve()),archive_sha256=sha(archive),members=inventory,
  member_count=len(inventory),exact_object_matches=sum(bool(m['source_candidates']) for m in inventory),
  source_equivalence_verified=False,clean_rebuild_ready=False,gameplay_verified=False,
  ambiguous_members=[m['name'] for m in inventory if m['source_hash_conflict']],
  next_action='locate_missing_sources' if any(not m['source_candidates'] for m in inventory) else 'rebuild_source_controls')


def discover_records(archive,roots):
 """Find retained matching objects, then suggest adjacent source candidates.

 Byte identity proves archive membership, not that the neighboring source still
 generates that object. Every result is explicitly an unverified candidate.
 """
 inventory=members(archive);sizes={m['bytes'] for m in inventory};by_hash={}
 for m in inventory:by_hash.setdefault(m['sha256'],[]).append(m['name'])
 records=[];seen=set()
 for raw in roots:
  root=Path(raw).resolve()
  if not root.is_dir():raise ValueError('Object search root missing')
  for obj in root.rglob('*.o'):
   if obj.is_symlink() or not obj.is_file() or obj.stat().st_size not in sizes:continue
   obj=obj.resolve()
   if obj in seen:continue
   seen.add(obj);digest=sha(obj)
   for name in by_hash.get(digest,[]):
    candidates={Path(str(obj)[:-2]),obj.parent/name[:-2],obj.parent.parent/name[:-2]}
    for src in candidates:
     if src.suffix not in {'.cpp','.cxx','.cc','.c','.m','.mm','.S'} or src.is_symlink() or not src.is_file():continue
     records.append(dict(object=str(obj),source=str(src.resolve()),record='adjacent_source_candidate_not_equivalence',archive_member=name,equivalence_verified=False))
 return records
