"""Decode bounded section-copy chains into non-linear ELF memory segments.

Independent implementation from the observed four-word record format. Lombyte's
published section manifests are comparison evidence, not executable input.
No game offsets, section names, function addresses or repository code are embedded.
"""
import hashlib
import struct

HEADER = struct.Struct('<4I')
MAX_SECTIONS = 32
MAX_COPY = 16 << 20
RAM_BYTES = 32 << 20


def discover(data, *, origin, region_offset, region_bytes, region_base):
    """Return one uniquely qualified layout, or None when no format is proven.

    The caller's code census independently qualifies the code's file-to-memory
    mapping. A chain must agree with that mapping, contain its entry point,
    contain four or more non-overlapping sections and include explicit zero-fill.
    Ambiguous layouts fail; address-like data alone never becomes executable.
    """
    data=memoryview(data)
    if (origin<0 or region_offset<origin or region_bytes<=0 or
        region_offset+region_bytes>origin+len(data)):
        raise ValueError('Code census region is not backed by this discovery window')
    if len(data) > 32 << 20:
        raise ValueError('Section discovery window exceeds budget')
    records={}
    for offset in range((-origin) & 3, len(data)-HEADER.size+1, 4):
        address,size,kind,entry=HEADER.unpack_from(data,offset)
        if (kind not in (1,8) or not 0<size<=MAX_COPY or
            address & 3 or entry & 3 or address<0x100000 or
            address+size>RAM_BYTES or not 0x100000<=entry<RAM_BYTES or
            offset+HEADER.size+size>len(data)):
            continue
        records[offset]=(address,size,kind,entry)
    successor={off:off+HEADER.size+row[1] for off,row in records.items()}
    children={nxt for off,nxt in successor.items() if nxt in records and records[off][3]==records[nxt][3]}
    candidates=[]
    for start in sorted(set(records)-children):
        at=start; chain=[];entry=records[start][3]
        while at in records and records[at][3]==entry and len(chain)<=MAX_SECTIONS:
            address,size,kind,_=records[at]
            chain.append(dict(header_offset=origin+at,file_offset=origin+at+HEADER.size,
                              address=address,bytes=size,zero_fill=kind==8))
            at=successor[at]
        if not 4<=len(chain)<=MAX_SECTIONS or not any(s['zero_fill'] for s in chain):
            continue
        ordered=sorted(chain,key=lambda s:s['address'])
        if any(a['address']+a['bytes']>b['address'] for a,b in zip(ordered,ordered[1:])):
            continue
        code=[s for s in chain if not s['zero_fill'] and
              s['address']<=entry<s['address']+s['bytes'] and
              min(s['file_offset']+s['bytes'],region_offset+region_bytes)-max(s['file_offset'],region_offset)>=64 and
              region_base+s['file_offset']-region_offset==s['address']]
        if len(code)!=1:
            continue
        code=code[0]
        for s in chain:
            s['executable']=s is code
            begin=s['file_offset']-origin
            s['sha256']=None if s['zero_fill'] else hashlib.sha256(data[begin:begin+s['bytes']]).hexdigest()
        candidates.append(dict(schema=1,format='copy-chain-u32-v1',entry=entry,
                               segments=chain,code_mapping_qualified=True))
    if len(candidates)>1:
        raise ValueError('Ambiguous section-copy chains; refusing to guess')
    return candidates[0] if candidates else None


def build_elf(layout, data, *, origin):
    """Build a compiler input ELF preserving each section's actual load address."""
    segments=layout['segments'];n=len(segments);eh=52;ph=32;sh=40
    names=bytearray(b'\0');indexes=[]
    payloads=[];cursor=(eh+ph*n+63)&~63
    for i,s in enumerate(segments):
        name='.text' if s['executable'] else ('.bss' if s['zero_fill'] else f'.data{i}')
        indexes.append(len(names));names.extend(name.encode()+b'\0')
        if s['zero_fill']:
            payloads.append((0,b''));continue
        at=s['file_offset']-origin;blob=bytes(data[at:at+s['bytes']])
        if at<0 or len(blob)!=s['bytes'] or hashlib.sha256(blob).hexdigest()!=s['sha256']:
            raise ValueError('Section payload differs from qualified layout')
        cursor=(cursor+63)&~63;payloads.append((cursor,blob));cursor+=len(blob)
    strings_index=len(names);names.extend(b'.shstrtab\0');strings_at=cursor;cursor+=len(names)
    section_at=(cursor+3)&~3;out=bytearray(section_at+sh*(n+2))
    struct.pack_into('<16sHHIIIIIHHHHHH',out,0,b'\x7fELF\x01\x01\x01'+bytes(9),
                     2,8,1,layout['entry'],eh,section_at,0,eh,ph,n,sh,n+2,n+1)
    for i,(s,(offset,blob),name) in enumerate(zip(segments,payloads,indexes)):
        flags=5 if s['executable'] else 6
        struct.pack_into('<8I',out,eh+i*ph,1,offset,s['address'],s['address'],len(blob),s['bytes'],flags,4)
        struct.pack_into('<10I',out,section_at+(i+1)*sh,name,8 if s['zero_fill'] else 1,
                         6 if s['executable'] else 3,s['address'],offset,s['bytes'],0,0,4,0)
        out[offset:offset+len(blob)]=blob
    out[strings_at:strings_at+len(names)]=names
    struct.pack_into('<10I',out,section_at+(n+1)*sh,strings_index,3,0,0,strings_at,len(names),0,0,1,0)
    return bytes(out)
