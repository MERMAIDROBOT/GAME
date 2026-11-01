"""Bounded Mach-O text-symbol fingerprints for source-rebuild diagnostics.

Instruction bytes only: relocations, data and runtime behavior are separate.
"""
from pathlib import Path
import hashlib,struct


def fingerprints(path):
 data=Path(path).read_bytes()
 if len(data)<32 or data[:4]!=b'\xcf\xfa\xed\xfe':raise ValueError('Expected little-endian 64-bit Mach-O object')
 ncmds,command_bytes=struct.unpack_from('<II',data,16)
 if ncmds>10000 or 32+command_bytes>len(data):raise ValueError('Invalid Mach-O command bounds')
 pos=32;section_index=0;text=None;table=None
 for _ in range(ncmds):
  if pos+8>32+command_bytes:raise ValueError('Truncated Mach-O load command')
  cmd,size=struct.unpack_from('<II',data,pos)
  if size<8 or pos+size>32+command_bytes:raise ValueError('Invalid Mach-O load command size')
  if cmd==0x19:
   if size<72:raise ValueError('Truncated segment')
   count=struct.unpack_from('<I',data,pos+64)[0]
   if 72+count*80>size:raise ValueError('Truncated section table')
   for i in range(count):
    section_index+=1;start=pos+72+i*80;name,segment,addr,length,offset=struct.unpack_from('<16s16sQQI',data,start)
    if name.rstrip(b'\0')==b'__text' and segment.rstrip(b'\0')==b'__TEXT':
     if text is not None or offset+length>len(data):raise ValueError('Invalid/duplicate Mach-O text section')
     text=(section_index,addr,data[offset:offset+length])
  if cmd==2:
   if size<24 or table is not None:raise ValueError('Invalid symbol table command')
   table=struct.unpack_from('<IIII',data,pos+8)
  pos+=size
 if text is None or table is None:raise ValueError('Missing text or symbols')
 index,base,code=text;symoff,count,stroff,strsize=table
 if count>1000000 or symoff+count*16>len(data) or stroff+strsize>len(data):raise ValueError('Invalid symbol table bounds')
 strings=data[stroff:stroff+strsize];groups={}
 for i in range(count):
  name_offset,kind,section,description,address=struct.unpack_from('<IBBHQ',data,symoff+i*16)
  if kind&0xe0 or kind&0xe!=0xe or section!=index:continue
  if name_offset>=len(strings):raise ValueError('Invalid symbol name offset')
  end=strings.find(b'\0',name_offset)
  if end<0:raise ValueError('Unterminated symbol name')
  offset=address-base
  if not 0<=offset<=len(code):raise ValueError('Text symbol outside section')
  name=strings[name_offset:end].decode('utf-8','replace');groups.setdefault(offset,[]).append(name)
 result={};starts=sorted(groups)
 for i,start in enumerate(starts):
  end=starts[i+1] if i+1<len(starts) else len(code);blob=code[start:end]
  for name in groups[start]:
   if name in result:raise ValueError('Duplicate text symbol name')
   result[name]=dict(bytes=len(blob),sha256=hashlib.sha256(blob).hexdigest())
 return result


def differences(built,reference):
 a=fingerprints(built);b=fingerprints(reference);shared=a.keys()&b.keys()
 return dict(scope='Mach-O text symbols only; instruction relocations/data/runtime not verified',matched_symbols=sum(a[n]==b[n] for n in shared),changed=[dict(symbol=n,built=a[n],reference=b[n]) for n in sorted(shared) if a[n]!=b[n]],added=sorted(a.keys()-b.keys()),missing=sorted(b.keys()-a.keys()))
