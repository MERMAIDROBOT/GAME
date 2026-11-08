"""sceGsExecStoreImage/LoadImage: libgraph's vram_addr ("word address / 64") is already the GS block number, so
BITBLTBUF SBP/DBP use it unscaled (the old *2048/256 made a photo at block 3000 read block 0, the framebuffer).
StoreImage no longer depends on guest heap space: titles that claim the whole heap at boot (Kelly Slater) fell
back to -1 and showed no photo; it now uses one packet reserved in the async arena. The native GS test covers
unscaled DBP, readback from block 3000 with the heap exhausted, and a block-0 negative control."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/gs-image-block-units.patch'


class Content(unittest.TestCase):
    def test_units_and_fallback(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('+        uint32_t sbp = static_cast<uint32_t>(img.vram_addr);', text)
        self.assertIn('+        uint32_t dbp = static_cast<uint32_t>(img.vram_addr);', text)
        self.assertIn('-        uint32_t sbp = (static_cast<uint32_t>(img.vram_addr) * 2048u) / 256u;', text)
        self.assertIn('reserveAsyncCallbackStack(80u + 16u, 16u)', text)
        self.assertIn('sceGsExecStoreImage should succeed without guest heap space', text)
        self.assertIn('block 0 should not hold the uploaded image', text)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_gs_image_block_units.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
