import struct
import unittest
from tim2 import decode_tim2,scan_tim2

class TIM2(unittest.TestCase):
    def data(self,kind=3,pixels=b'\x20\x40\x60\x80',clut=b'',colors=0,ctype=0,width=1,height=1):
        header=b'TIM2'+struct.pack('<BBH',4,0,1)+bytes(8)
        picture=struct.pack('<IIIHHBBBBHHQQII',48+len(pixels)+len(clut),len(clut),len(pixels),48,colors,0,1,ctype,kind,width,height,0,0,0,0)
        return header+picture+pixels+clut
    def test_direct_alpha_and_offset(self):
        image,meta=decode_tim2(b'prefix'+self.data(),6)
        self.assertEqual(image.getpixel((0,0)),(32,64,96,255));self.assertEqual(meta['offset'],6)
    def test_palette_bank_order(self):
        palette=b''.join(bytes((i,0,0,128)) for i in range(256))
        image,_=decode_tim2(self.data(5,b'\x08',palette,256,3))
        self.assertEqual(image.getpixel((0,0)),(16,0,0,255))
    def test_compound_four_bit_palette_all_indices(self):
        palette=b''.join(bytes((i,40,80,128)) for i in range(32))
        packed=bytes((i | ((i+1)<<4)) for i in range(0,16,2))
        image,_=decode_tim2(self.data(4,packed,palette,32,0x43,width=16))
        self.assertEqual([p[0] for p in image.getdata()],list(range(8))+list(range(16,24)))
        self.assertEqual(set(p[3] for p in image.getdata()),{255})
    def test_linear_four_bit_palette_does_not_reorder(self):
        palette=b''.join(bytes((i,40,80)) for i in range(16))
        image,_=decode_tim2(self.data(4,b'\x98',palette,16,2,width=2))
        self.assertEqual([p[0] for p in image.getdata()],[8,9])
    def test_16bit_palette_channels_and_alpha(self):
        palette=struct.pack('<256H',0x801f,0x83e0,0xfc00,0x7fff,*([0]*252))
        image,_=decode_tim2(self.data(5,bytes((0,1,2,3)),palette,256,1,width=4))
        self.assertEqual(list(image.getdata()),[(248,0,0,255),(0,248,0,255),(0,0,248,255),(248,248,248,0)])
    def test_mip_size_table_and_level_selection(self):
        header=b'TIM2'+struct.pack('<BBH',4,0,1)+bytes(8)
        palette=b''.join(bytes((i,0,0)) for i in range(256))
        picture=struct.pack('<IIIHHBBBBHHQQII',80+5+768,768,5,80,256,0,2,2,5,2,2,0,0,0,0)
        mip=bytes(16)+struct.pack('<II',4,1)+bytes(8)
        data=header+picture+mip+bytes((0,1,2,3,7))+palette
        image,meta=decode_tim2(data,level=1)
        self.assertEqual(image.size,(1,1));self.assertEqual(image.getpixel((0,0)),(7,0,0,255));self.assertEqual(meta['authored_mips'],2)
        bad=bytearray(data);struct.pack_into('<I',bad,16+64,100)
        with self.assertRaisesRegex(ValueError,'mip'):decode_tim2(bad)
    def test_bad_compound_palette_and_initial_bank_reject(self):
        palette=b''.join(bytes((i,0,0,128)) for i in range(16))
        with self.assertRaisesRegex(ValueError,'palette'):decode_tim2(self.data(4,b'\x00',palette,16,0x43))
        data=bytearray(self.data());struct.pack_into('<Q',data,16+24,1<<56)
        # A direct-color texture has no palette bank, so qualify this on indexed input.
        palette=b''.join(bytes((i,0,0,128)) for i in range(256))
        data=bytearray(self.data(5,b'\x00',palette,256,3));struct.pack_into('<Q',data,16+24,1<<56)
        with self.assertRaisesRegex(ValueError,'bank'):decode_tim2(data)
    def test_128_aligned_compound_rgb24_palette_padding(self):
        header=b'TIM2'+struct.pack('<BBH',4,1,1)+bytes(120)
        palette=b''.join(bytes((i,50,90)) for i in range(32))+bytes(32)
        picture=struct.pack('<IIIHHBBBBHHQQII',128+1+128,128,1,128,32,0,1,0x42,4,1,1,0,0,0,0)+bytes(80)
        image,_=decode_tim2(header+picture+b'\x08'+palette)
        self.assertEqual(image.getpixel((0,0)),(16,50,90,255))
    def test_truncated_or_unknown_rejected(self):
        with self.assertRaises(ValueError):decode_tim2(self.data()[:-1])
        with self.assertRaises(ValueError):decode_tim2(self.data(kind=9))
    def test_scanning_skips_false_magic(self):
        self.assertEqual(len(list(scan_tim2(b'TIM2junk'+self.data()))),1)
if __name__=='__main__':unittest.main()
