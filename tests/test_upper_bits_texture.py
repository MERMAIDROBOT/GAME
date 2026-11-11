"""A texture of the upper bits (T8H, T4HL, T4HH) over a 24-bit colour or depth buffer is not made stale by drawing.

The GS writes bits 0-23 of a CT24 or Z24 buffer and leaves bits 24-31 alone. SSX 3 keeps HUD textures in the top
byte of its Z24 buffer and draws the HUD with depth written, so the GPU bridge took each HUD primitive's depth
write as new texels: one 256x256 T8H texture was written back from the GPU and decoded about 16 times a frame.
In an SSX 3 race the renderer's GPU time fell from 13.4 to 4.7 ms a frame with the HUD unchanged. Other formats,
and upper-bit textures over 32-bit buffers (whose drawing does write bits 24-31), are still invalidated."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'runtime/gpu/host-adapter/gpu_bridge.cpp'


class UpperBitsTexture(unittest.TestCase):
    def test_draws_into_24_bit_buffers_keep_upper_bit_textures(self):
        text = BRIDGE.read_text()
        start = text.index('u64 CachedTexture(Bridge& b')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('const bool upper_bits = psm == gs::PSMT8H || psm == gs::PSMT4HL || psm == gs::PSMT4HH;', body)
        rule = re.search(r'for \(const Owned\* o : t\.over\)\s*if \((.*?)\) t\.valid = false;', body, re.S)
        self.assertIsNotNone(rule)
        condition = ' '.join(rule.group(1).split())
        self.assertIn('o->drawn_serial > t.decoded_serial', condition)
        self.assertIn('!(upper_bits && (o->psm == gs::PSMCT24 || o->psm == gs::PSMZ24))', condition)
        # Only 24-bit buffers: a CT32 or Z32 buffer's drawing writes the top byte.
        self.assertNotIn('PSMCT32', condition)
        self.assertNotIn('PSMZ32', condition)

    def test_transfers_still_invalidate(self):
        # Memory written by an image transfer reaches upper-bit textures through the texture pages, unchanged.
        text = BRIDGE.read_text()
        self.assertIn('if (t->overlaps(first,last)) t->valid = false;', text)


if __name__ == '__main__':
    unittest.main()
