"""One process-wide build slot shared by installed copies of the engine.

The OS releases the lock on exit/crash. Foreign tools still need the existing
process guard and coordination; this lock never overrides active user play.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import errno,json,os,stat,tempfile,subprocess
if os.name=='nt':import msvcrt
else:import fcntl


@contextmanager
def slot(kind,*,path=None):
 path=Path(path) if path is not None else Path(tempfile.gettempdir())/'ps2-port-engine-compute.lock'
 flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)
 fd=os.open(path,flags,0o600)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or (hasattr(os,'getuid') and info.st_uid!=os.getuid()) or info.st_nlink!=1:raise ValueError('Unsafe engine compute lock')
  try:
   if os.name=='nt':
    if info.st_size==0:os.write(fd,b' ');os.lseek(fd,0,os.SEEK_SET)
    msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
   else:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError as error:
   if error.errno not in {errno.EAGAIN,errno.EACCES}:raise
   raise RuntimeError('Another engine build/control operation owns the compute slot') from None
  offset=1 if os.name=='nt' else 0
  os.ftruncate(fd,offset);os.lseek(fd,offset,os.SEEK_SET);os.write(fd,json.dumps(dict(pid=os.getpid(),operation=kind)).encode())
  yield
 finally:os.close(fd)


def exclusive(kind,*,when=None):
 def decorate(fn):
  @wraps(fn)
  def wrapped(*args,**kwargs):
   if when is not None and not kwargs.get(when,False):return fn(*args,**kwargs)
   with slot(kind):return fn(*args,**kwargs)
  return wrapped
 return decorate


# macOS uses "Blender" while Linux commonly uses "blender". All entry points
# share this list; source-built game/test artifact names must be recognized too.
COMPUTE_NAMES = frozenset(name.casefold() for name in (
    'ps2EntryRunner','kelly_source_runner','kelly_runtime_checks','ps2_analyzer','ps2_recomp',
    'ps2PortRuntimeTests','ninja','clang','clang++','make','gmake','cmake','meson','xcodebuild',
    'blender','realesrgan-ncnn-vulkan','ViewportProbe','GameWindowRecorder',
    'swift','swiftc','swift-frontend'))

def conflicting_processes(listing):
    return [line.strip() for line in listing.splitlines()
            if Path(line.strip()).name.casefold() in COMPUTE_NAMES]

def assert_idle():
    active=subprocess.check_output(['/bin/ps','-axo','comm='],text=True,timeout=10)
    if conflicting_processes(active):
        raise RuntimeError('Game/build/render slot is occupied; no processes were stopped')
