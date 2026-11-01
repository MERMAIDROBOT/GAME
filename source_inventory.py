"""Content verification with directory-relative, no-follow reads on POSIX.

No timestamp cache: every pinned file is hashed. Other platforms retain the
original path validator. Directory descriptors avoid repeated ancestor walks.
"""
from pathlib import Path
from collections import defaultdict
import hashlib,os,re,stat

COMPILED_SUFFIXES={'.o','.a','.dylib','.so','.dll','.exe','.pch','.zip','.bc','.obj','.lib','.spv'}
MAGICS=(b'BC\xc0\xde',b'\xde\xc0\x17\x0b',b'!<arch>\n',b'!<thin>\n',b'\xcf\xfa\xed\xfe',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xfe\xed\xfa\xce',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\x03\x02\x23\x07',b'\x07\x23\x02\x03')
APPLEDOUBLE=b'\x00\x05\x16\x07\x00\x02\x00\x00'


def compiled_magic(header):return header.startswith(MAGICS)
def descriptor_reads():
 return os.open in os.supports_dir_fd and os.scandir in os.supports_fd and hasattr(os,'O_NOFOLLOW') and hasattr(os,'O_DIRECTORY')
def directory_flags():return os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|getattr(os,'O_CLOEXEC',0)
def file_flags():return os.O_RDONLY|os.O_NOFOLLOW|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_CLOEXEC',0)


def open_directory(root,parts):
 opened=[]
 try:
  fd=os.open(root,directory_flags());opened.append(fd)
  for part in parts:fd=os.open(part,directory_flags(),dir_fd=fd);opened.append(fd)
  return fd,opened
 except BaseException:
  for fd in reversed(opened):os.close(fd)
  raise


def verify_files(root,files):
 from clean_build import relative,path_in,compiled_binary,sha
 root=Path(root);groups=defaultdict(list)
 # An inventoried "._X" next to an inventoried X on a non-Apple volume is the AppleDouble store of X's extended attributes,
 # not source. macOS rewrites it whenever X is copied (com.apple.provenance names the copying app), so its bytes cannot be
 # pinned: such entries must still exist as regular AppleDouble files but are not hash-compared. Nothing compiles them.
 sidecars={name for name in files if relative(name).name.startswith('._') and str(relative(name).with_name(relative(name).name[2:])) in files}
 for name,pin in files.items():
  path=relative(name)
  if not isinstance(pin,str) or not re.fullmatch('[a-f0-9]{64}',pin):raise ValueError('Missing or changed source input: '+name)
  if path.suffix.lower() in COMPILED_SUFFIXES:raise ValueError('Historical compiled input is forbidden: '+name)
  groups[path.parent.parts].append((path.name,name,pin))
 if not descriptor_reads():
  for group in groups.values():
   for leaf,name,pin in group:
    p=path_in(root,name)
    if name in sidecars:
     if p.is_symlink() or not p.is_file() or p.read_bytes()[:8]!=APPLEDOUBLE:raise ValueError('Missing or changed source input: '+name)
     continue
    if compiled_binary(p):raise ValueError('Historical compiled input is forbidden: '+name)
    if not p.is_file() or sha(p)!=pin:raise ValueError('Missing or changed source input: '+name)
  return
 for parts,group in groups.items():
  opened=[]
  try:
   parent,opened=open_directory(root,parts)
   for leaf,name,pin in group:
    fd=os.open(leaf,file_flags(),dir_fd=parent)
    with os.fdopen(fd,'rb') as stream:
     if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Source input is not a regular file: '+name)
     first=stream.read(1024*1024)
     if name in sidecars:
      if not first.startswith(APPLEDOUBLE):raise ValueError('Missing or changed source input: '+name)
      continue
     if compiled_magic(first):raise ValueError('Historical compiled input is forbidden: '+name)
     digest=hashlib.sha256(first)
     while block:=stream.read(1024*1024):digest.update(block)
     if digest.hexdigest()!=pin:raise ValueError('Missing or changed source input: '+name)
  except OSError as error:raise ValueError('Missing or unsafe source input in '+str(Path(*parts))+': '+str(error)) from error
  finally:
   for fd in reversed(opened):os.close(fd)


def audit_include(root,folder,files,companions):
 from clean_build import relative,path_in,filesystem_metadata
 root=Path(root);parts=relative(folder).parts
 if not descriptor_reads():
  parent=path_in(root,folder)
  if not parent.is_dir():raise ValueError('Missing include directory')
  for p in parent.rglob('*'):
   if p.is_symlink():raise ValueError('Include directory contains a symlink')
   if p.is_file() and str(p.relative_to(root)) not in files:
    companion=p.with_name(p.name[2:]) if p.name.startswith('._') else None
    if companion is not None and str(companion.relative_to(root)) in companions and filesystem_metadata(p):continue
    raise ValueError('Unpinned include input: '+str(p))
   if not p.is_file() and not p.is_dir():raise ValueError('Include directory contains a special file')
  return
 opened=[]
 def walk(fd,prefix):
  with os.scandir(fd) as entries:
   for entry in entries:
    name=str(prefix/entry.name)
    if entry.is_symlink():raise ValueError('Include directory contains a symlink')
    if entry.is_dir(follow_symlinks=False):
     child=os.open(entry.name,directory_flags(),dir_fd=fd)
     try:walk(child,prefix/entry.name)
     finally:os.close(child)
    elif entry.is_file(follow_symlinks=False):
     if name in files:continue
     companion=entry.name[2:] if entry.name.startswith('._') else None
     known=companion is not None and str(prefix/companion) in companions
     if known:
      info=os.stat(companion,dir_fd=fd,follow_symlinks=False)
      known=stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
     if known:
      item=os.open(entry.name,file_flags(),dir_fd=fd)
      with os.fdopen(item,'rb') as stream:
       if stat.S_ISREG(os.fstat(stream.fileno()).st_mode) and stream.read(8)==APPLEDOUBLE:continue
     raise ValueError('Unpinned include input: '+name)
    else:raise ValueError('Include directory contains a special file')
 try:
  fd,opened=open_directory(root,parts);walk(fd,Path(folder))
 except OSError as error:raise ValueError('Missing or unsafe include directory: '+str(error)) from error
 finally:
  for fd in reversed(opened):os.close(fd)
