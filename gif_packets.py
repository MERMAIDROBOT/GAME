"""Bounded GIF stream structural inspector; IMAGE2 (FLG=3) aliases IMAGE.

Input is a complete sequence of GIF tags and payloads, not DMA chain bytes.
This independent parser does not interpret GS register effects or certify pixels.
"""
import struct


def inspect(data):
    if len(data)%16: raise ValueError('GIF stream must be quadword aligned')
    offset=0;packets=[]
    while offset<len(data):
        start=offset
        low,high=struct.unpack_from('<QQ',data,offset);offset+=16
        nloop=low&0x7fff;mode=(low>>58)&3;nreg=(low>>60)&15 or 16
        words=nloop*nreg if mode==0 else (nloop*nreg+1)//2 if mode==1 else nloop
        size=words*16
        if offset+size>len(data): raise ValueError('Truncated GIF payload at byte '+str(start))
        packets.append(dict(offset=start,nloop=nloop,nreg=nreg,mode=mode,
                            kind=('packed','reglist','image','image')[mode],payload_bytes=size))
        offset+=size
    return dict(packets=packets,image_packets=sum(p['kind']=='image' for p in packets),
                image_bytes=sum(p['payload_bytes'] for p in packets if p['kind']=='image'),
                coverage='Packet structure only; no DMA, VRAM state or rendered image validation')
