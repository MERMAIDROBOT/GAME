"""VIF1 DIRECT/DIRECTHL: PATH2 is a byte stream; any GIF packet (PACKED, REGLIST or IMAGE) may continue in the payload
of the following DIRECT commands, so whole tag+payload units are reassembled before submission. The old interpreter instead took the raw bytes after the first DIRECT as image data, swallowing the next
DIRECT header and desynchronising the VIF stream (observed: texture bytes decoded as an UNPACK with the
interrupt bit, VIF1 stalled, the DMA marker wait timed out on a purple screen). A DIRECT split across VIF1 data
chunks is tracked separately. The native test (tests/runtime_timer.cpp, testVif1DirectImageSpan) fails on the old
interpreter and passes with this patch."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/vif1-direct-image-span.patch'


class Content(unittest.TestCase):
    def test_direct_payload_and_split(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('+void PS2Memory::submitVif1DirectPayload(const uint8_t *data, uint32_t qwCount, bool directHl)', text)
        self.assertIn('+        if (m_vif1PendingDirectQwc != 0u)', text)
        self.assertIn('-        if (m_vif1PendingPath2ImageQwc != 0u)', text)
        self.assertIn('m_vif1Path2Stream', text)
        self.assertIn('PACKED continuation was not applied', (ROOT / 'tests/runtime_timer.cpp').read_text())
        self.assertIn('testVif1DirectImageSpan();', (ROOT / 'tests/runtime_timer.cpp').read_text())

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_vif1_direct_image_span.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
