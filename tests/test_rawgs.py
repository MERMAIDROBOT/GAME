import struct
import tempfile
import unittest
from pathlib import Path
import rawgs

class CaptureTests(unittest.TestCase):
    def capture(self, ops):
        data = b'PGSCAP1\0' + struct.pack('<II',1,0)
        for i,(op,payload) in enumerate(ops,1):
            data += struct.pack('<IIQ',op,len(payload),i)+payload
        return data
    def validate(self,data):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'capture';path.write_bytes(data)
            return rawgs.validate(path)
    def test_complete_and_unsupported(self):
        vram=bytes(rawgs.VRAM_SIZE)
        result=self.validate(self.capture([(1,vram),(2,bytes(16)),(6,vram)]))
        self.assertTrue(result['replayable']);self.assertEqual(result['record_count'],3)
        result=self.validate(self.capture([(1,vram),(5,b'host clear'),(6,vram)]))
        self.assertFalse(result['replayable']);self.assertEqual(result['unsupported_operations'],{'host clear':1})
    def test_incomplete_trailing_and_sequence(self):
        vram=bytes(rawgs.VRAM_SIZE)
        valid=self.capture([(1,vram),(6,vram)])
        for data in (valid[:-1],valid+b'x',self.capture([(1,vram)]),self.capture([(6,vram)]),
                     valid[:24]+struct.pack('<Q',8)+valid[32:]):
            with self.subTest(size=len(data)),self.assertRaises(ValueError): self.validate(data)
    def test_bad_payload_and_header(self):
        vram=bytes(rawgs.VRAM_SIZE)
        for op,payload in ((2,bytes(17)),(3,struct.pack('<QQ',128,0)),(4,bytes(32)),(8,b'')):
            with self.subTest(op=op),self.assertRaises(ValueError):
                self.validate(self.capture([(1,vram),(op,payload),(6,vram)]))
        with self.assertRaises(ValueError):self.validate(bytes(16))

    def test_split_gif_is_not_replayable(self):
        vram=bytes(rawgs.VRAM_SIZE)
        # One packed register quadword is promised but absent.
        tag=struct.pack('<QQ',1 | (1<<60),0)
        result=self.validate(self.capture([(1,vram),(2,tag),(6,vram)]))
        self.assertFalse(result['replayable'])
        self.assertEqual(result['incomplete_gif_packets'],1)

    def test_v2_environment_and_ingress(self):
        vram=bytes(rawgs.VRAM_SIZE)
        def v2(ops):
            return self.capture(ops).replace(b'PGSCAP1\0'+struct.pack('<II',1,0),b'PGSCAP2\0'+struct.pack('<II',2,1),1)
        result=self.validate(v2([(1,vram),(7,struct.pack('<Q',0xc00000)),(8,bytes(16)),(6,vram)]))
        self.assertTrue(result['eligible_for_strict_cpu_comparison'])
        for ops in ([(1,vram),(8,bytes(16)),(6,vram)],[(1,vram),(7,bytes(7)),(6,vram)]):
            with self.assertRaises(ValueError):self.validate(v2(ops))
        with self.assertRaises(ValueError):self.validate(self.capture([(1,vram),(7,bytes(8)),(6,vram)]))
