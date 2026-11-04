"""PS2_PORT_DMA_WATCH=1 adds the registers a graphics "sync path" spin-wait polls (VIF1/GIF CHCR, VIF1_STAT,
GIF_STAT, D_STAT) to the runtime heartbeat, so a path that never reports idle can be named. Off by default."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/dma-watch.patch'


class Content(unittest.TestCase):
    def test_opt_in_registers(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('std::getenv("PS2_PORT_DMA_WATCH") != nullptr', text)
        for register in ('0x10009000u', '0x1000A000u', '0x10003C00u', '0x10003020u', '0x1000E010u'):
            self.assertIn(register, text)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_dma_watch.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
