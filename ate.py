"""Read-only bounded ATEX split-TIM2 animation extraction.

ATEX stores names and separate header/image/palette offsets. Shared ranges are
intentional; do not search for embedded TIM2 magic to enumerate these frames.
"""
import struct
from tim2 import decode_tim2


def entries(data, offset=0):
    if offset < 0 or offset+16 > len(data) or data[offset:offset+4] != b'ATEX':
        raise ValueError('Not ATEX')
    version, layout_marker, count = struct.unpack_from('<HHI', data, offset+4)
    if version != 0 or layout_marker != 60 or not 1 <= count <= 4096:
        raise ValueError('Unsupported ATEX header')
    end_table = 16+count*48
    if offset+end_table > len(data):
        raise ValueError('Truncated ATEX entries')
    result=[]
    for i in range(count):
        p=offset+16+i*48
        raw_name=data[p:p+32]
        if b'\0' not in raw_name:
            raise ValueError('Unterminated ATEX name')
        try:
            name=raw_name.split(b'\0',1)[0].decode('ascii')
        except UnicodeDecodeError as e:
            raise ValueError('Invalid ATEX name') from e
        if not name or any(ord(c)<32 for c in name):
            raise ValueError('Invalid ATEX name')
        ho,io,po=struct.unpack_from('<III',data,p+32)
        if ho < end_table or offset+ho+48 > len(data):
            raise ValueError('Invalid ATEX picture header range')
        total,clut_bytes,image_bytes,header=struct.unpack_from('<IIIH',data,offset+ho)
        if not 48 <= header <= 4096 or total != header+image_bytes+clut_bytes:
            raise ValueError('Invalid ATEX picture sizes')
        if not 1 <= image_bytes <= 64*1024**2 or clut_bytes > 4096:
            raise ValueError('Excessive ATEX image or palette')
        for start,size in ((ho,header),(io,image_bytes),(po,clut_bytes)):
            if size and (start < end_table or offset+start+size > len(data)):
                raise ValueError('Truncated ATEX split range')
        result.append(dict(index=i,name=name,header_offset=ho,image_offset=io,
                           palette_offset=po,header_bytes=header,image_bytes=image_bytes,
                           palette_bytes=clut_bytes))
    return result


def tim2_bytes(data, offset, entry):
    """Assemble a qualified entry for the existing TIM2 decoder, without edits."""
    parts=[]
    for key,size in (('header_offset','header_bytes'),('image_offset','image_bytes'),
                     ('palette_offset','palette_bytes')):
        begin=offset+entry[key];parts.append(data[begin:begin+entry[size]])
    return b'TIM2'+struct.pack('<BBH',4,0,1)+bytes(8)+b''.join(parts)


def decode_entry(data, offset, index):
    rows=entries(data,offset)
    if type(index) is not int or not 0 <= index < len(rows):
        raise ValueError('Invalid ATEX frame index')
    image,meta=decode_tim2(tim2_bytes(data,offset,rows[index]))
    return image,dict(rows[index],**meta,ate_offset=offset)


def scan_ate(data):
    """Discover qualified animation frames, including shared image/palette ranges.

    Like scan_tim2, this is signature discovery rather than an archive parser.
    Unsupported containers are skipped; decoding never changes the source.
    """
    offset=0
    while True:
        offset=data.find(b'ATEX',offset)
        if offset<0:
            return
        try:
            rows=entries(data,offset)
        except ValueError:
            offset+=4
            continue
        for row in rows:
            try:
                image,meta=decode_tim2(tim2_bytes(data,offset,row))
            except ValueError:
                continue
            yield image,dict(row,**meta,ate_offset=offset)
        offset+=4
