import struct
import unittest
from gif_packets import inspect


def tag(mode,nloop,nreg=1):
    return struct.pack('<QQ',nloop|(mode<<58)|(nreg<<60),0)


class GifPackets(unittest.TestCase):
    def test_both_image_modes_consume_payload_and_next_tag(self):
        for mode in (2,3):
            # Payload deliberately resembles a GIF tag: it must not be parsed.
            stream=tag(mode,2)+tag(3,32767)+bytes(16)+tag(0,0)
            r=inspect(stream)
            self.assertEqual(len(r['packets']),2);self.assertEqual(r['image_bytes'],32)
            self.assertEqual(r['packets'][1]['offset'],48)

    def test_odd_reglist_and_zero_nreg_encoding(self):
        r=inspect(tag(1,1,3)+bytes(32)+tag(0,1,0)+bytes(256))
        self.assertEqual([p['payload_bytes'] for p in r['packets']],[32,256])

    def test_truncation_and_alignment_fail_closed(self):
        for stream in (b'x',tag(3,2)+bytes(16),tag(0,1,0)+bytes(16)):
            with self.assertRaises(ValueError):inspect(stream)
