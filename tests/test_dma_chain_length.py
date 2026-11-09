"""A DMA source chain runs until END/REFE/RET or a TIE interrupt, however many tags it has. The upstream
interpreter stopped after 4096 tags; SSX 3's race VIF1 chains (650-770 KB) went past that, so their tail was
dropped: rider heads and other late geometry never reached the GS, and a lost STCYCL reset left the next
renderer unpacking its header sparsely (garbage GIF tags, aborted XGKICKs). Only a runaway chain (more tags
or payload than main memory) is now bounded, and it reports itself. The native test (tests/runtime_timer.cpp,
testLongVif1Chain) fails on the old interpreter and passes with this patch."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/dma-chain-length.patch'


class Content(unittest.TestCase):
    def test_tag_cap_removed(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('-                    const int kMaxChainTags = 4096;', text)
        self.assertIn('+                    const size_t kMaxChainBytes = PS2_RAM_SIZE;', text)
        self.assertIn('without an end tag', text)
        runtime_test = (ROOT / 'tests/runtime_timer.cpp').read_text()
        self.assertIn('testLongVif1Chain();', runtime_test)
        self.assertIn('stopped before its END tag', runtime_test)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_dma_chain_length.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
