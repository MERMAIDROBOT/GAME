"""Inspect explicit expected-audible local PCM evidence, never certify sound quality."""
from pathlib import Path
import array,hashlib,math,sys,wave

def inspect(path):
 p=Path(path)
 if p.is_symlink() or not p.is_file() or not 44<=p.stat().st_size<=128*1024*1024:
  raise ValueError('Expected audio evidence must be a regular PCM WAV up to128MiB')
 digest=hashlib.sha256(p.read_bytes()).hexdigest()
 with wave.open(str(p),'rb') as w:
  channels,rate,width,frames=w.getnchannels(),w.getframerate(),w.getsampwidth(),w.getnframes()
  if w.getcomptype()!='NONE' or width!=2 or channels not in {1,2} or not 8000<=rate<=192000 or not 1<=frames/rate<=600:
   raise ValueError('Audio evidence needs1..600seconds of mono/stereo signed16bit PCM')
  count=total=peak=0
  while raw:=w.readframes(8192):
   if len(raw)%(channels*2):raise ValueError('Incomplete audio PCM frame')
   values=array.array('h');values.frombytes(raw)
   if sys.byteorder!='little':values.byteswap()
   count+=len(values);total+=sum(v*v for v in values);peak=max(peak,max(map(abs,values),default=0))
  if count!=frames*channels:raise ValueError('Truncated audio evidence')
 if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Audio evidence changed during inspection')
 return dict(sha256=digest,duration_seconds=frames/rate,channels=channels,sample_rate=rate,peak=peak,rms=math.sqrt(total/count),entirely_silent=peak==0,quality_verified=False,speech_verified=False,music_verified=False)
