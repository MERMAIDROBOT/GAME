"""Bounded decoder for single-picture linear TIM2 direct/indexed textures and mip levels.

Layout cross-checked against ImageMagick's TIM2 reader; rejects other formats.
No game-specific bytes, guessed swizzles, or archive mutation.
"""
import struct
from PIL import Image


def decode_tim2(data, offset=0, level=0):
    if offset < 0 or offset + 16 > len(data) or data[offset:offset+4] != b'TIM2':
        raise ValueError('Not TIM2')
    version, alignment, count = struct.unpack_from('<BBH', data, offset+4)
    if version != 4 or alignment not in (0,1) or count != 1:
        raise ValueError('Unsupported TIM2 file layout')
    start = offset + (128 if alignment else 16)
    if start+48 > len(data):raise ValueError('Truncated picture header')
    total,clut_bytes,image_bytes,header,colors,fmt,mips,clut_type,kind,w,h = struct.unpack_from('<IIIHHBBBBHH',data,start)
    if fmt!=0 or not 1<=mips<=7 or header<48 or not w or not h or w*h>1024*1024:
        raise ValueError('Unsupported TIM2 picture format or dimensions')
    if type(level) is not int or not 0<=level<mips:
        raise ValueError('Invalid mip level')
    if total<header+image_bytes+clut_bytes or start+total>len(data):
        raise ValueError('Truncated TIM2 picture')
    bpp={1:16,2:24,3:32,4:4,5:8}.get(kind)
    if not bpp:raise ValueError('Unsupported TIM2 pixel type')
    if (w>>(mips-1))==0 or (h>>(mips-1))==0:
        raise ValueError('Mip dimensions become zero')
    expected=[((w>>i)*(h>>i)*bpp+7)//8 for i in range(mips)]
    if mips>1:
        if header<64+4*mips:raise ValueError('Truncated mip size table')
        sizes=list(struct.unpack_from('<'+str(mips)+'I',data,start+64))
        if any(size<need for size,need in zip(sizes,expected)) or sum(sizes)>image_bytes:
            raise ValueError('Invalid mip byte ranges')
    else:
        sizes=[image_bytes]
        if image_bytes!=expected[0]:raise ValueError('Unsupported image padding/layout')
    w,h=w>>level,h>>level
    begin=start+header+sum(sizes[:level]);raw=data[begin:begin+expected[level]]
    palette_start=start+header+image_bytes
    palette=data[palette_start:palette_start+clut_bytes]
    def color(chunk, storage):
        if storage==1:
            value=int.from_bytes(chunk,'little')
            return ((value&31)<<3,((value>>5)&31)<<3,((value>>10)&31)<<3,255 if value&0x8000 else 0)
        return tuple(chunk[:3])+(min(chunk[3]*2,255) if storage==3 else 255,)
    if kind in (1,2,3):
        stride={1:2,2:3,3:4}[kind]
        if colors or clut_bytes:raise ValueError('Unexpected palette for direct texture')
        pixels=[color(raw[i:i+stride],kind) for i in range(0,len(raw),stride)]
    else:
        storage=clut_type&63
        if storage not in (1,2,3) or clut_type&128 or clut_type&~0x43:
            raise ValueError('Unsupported CLUT storage mode')
        compound=bool(clut_type&64)
        if kind==5 and (colors!=256 or compound):raise ValueError('Unsupported 8-bit palette layout')
        if kind==4 and colors not in ((32,) if compound else (16,)):
            raise ValueError('Unsupported 4-bit palette layout')
        tex0=struct.unpack_from('<Q',data,start+24)[0]
        if (tex0>>56)&31:raise ValueError('Nonzero initial palette bank requires explicit qualification')
        stride={1:2,2:3,3:4}[storage]
        palette_bytes=colors*stride
        alignment_bytes=128 if alignment else 16
        padded_bytes=(palette_bytes+alignment_bytes-1)//alignment_bytes*alignment_bytes
        if clut_bytes not in {palette_bytes,padded_bytes}:raise ValueError('Invalid palette length')
        entries=[]
        for i in range(16 if kind==4 else 256):
            j=((i&~31)|((i&8)<<1)|((i&16)>>1)|(i&7)) if kind==5 or compound else i
            entries.append(color(palette[j*stride:(j+1)*stride],storage))
        indices=list(raw) if kind==5 else [n for byte in raw for n in (byte&15,byte>>4)][:w*h]
        pixels=[entries[i] for i in indices]
    image=Image.new('RGBA',(w,h));image.putdata(pixels)
    return image,dict(offset=offset,end=start+total,width=w,height=h,kind=kind,clut_type=clut_type,
                     authored_mips=mips,decoded_level=level,mip_byte_sizes=sizes)


def scan_tim2(data):
    """Find and validate self-describing TIM2 chunks; not a complete ST2 parser."""
    at=0
    while True:
        at=data.find(b'TIM2',at)
        if at<0:return
        try:
            image,metadata=decode_tim2(data,at)
        except ValueError:
            at+=4;continue
        yield image,metadata
        at=metadata['end']
