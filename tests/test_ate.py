import struct
import unittest
from ate import entries,decode_entry,scan_ate


def fixture():
    header=bytearray(48)
    struct.pack_into('<IIIHHBBBBHH',header,0,48+4+1024,1024,4,48,256,0,1,3,5,2,2)
    palette=bytearray(1024)
    for i in range(256):palette[i*4:i*4+4]=bytes((i,0,0,128))
    table=bytearray(96)
    for i in range(2):
        p=i*48;table[p:p+6]=f'f{i:04d}\0'.encode()
        struct.pack_into('<III',table,p+32,112,160,164)
    return bytearray(b'ATEX'+struct.pack('<HHII',0,60,2,0)+table+header+bytes((0,1,8,16))+palette)


class AteTests(unittest.TestCase):
    def test_shared_split_ranges_decode(self):
        data=fixture();rows=entries(data)
        self.assertEqual(len(rows),2)
        a,_=decode_entry(data,0,0);b,_=decode_entry(data,0,1)
        self.assertEqual(a.tobytes(),b.tobytes())
        self.assertEqual(list(a.getdata()),[(0,0,0,255),(1,0,0,255),(16,0,0,255),(8,0,0,255)])

    def test_embedded_container(self):
        data=b'prefix'+fixture()
        image,meta=decode_entry(data,6,1)
        self.assertEqual(image.size,(2,2));self.assertEqual(meta['ate_offset'],6)

    def test_malformed_and_truncated(self):
        for length in (0,15,111,159,163,len(fixture())-1):
            with self.assertRaises(ValueError):entries(fixture()[:length])
        for field,value in ((4,1),(6,30),(8,0),(8,4097),(48,0),(52,2**32-1)):
            data=fixture();struct.pack_into('<H' if field in (4,6) else '<I',data,field,value)
            with self.assertRaises(ValueError):entries(data)

    def test_bad_index(self):
        for i in (-1,2,True):
            with self.assertRaises(ValueError):decode_entry(fixture(),0,i)

    def test_discovery_recovers_after_false_signature_and_retains_shared_frames(self):
        data=b'ATEX'+bytes(16)+fixture()+b'ATEX'
        before=bytes(data)
        found=list(scan_ate(data))
        self.assertEqual([meta['name'] for _,meta in found],['f0000','f0001'])
        self.assertEqual([meta['ate_offset'] for _,meta in found],[20,20])
        self.assertEqual(found[0][0].tobytes(),found[1][0].tobytes())
        self.assertEqual(data,before)


if __name__=='__main__':unittest.main()
