"""IMAGE2 must survive every transport layer, not just packet inspection."""
from pathlib import Path
import unittest
import native
import pipeline

ROOT=Path(__file__).resolve().parents[1]
class Image2Patch(unittest.TestCase):
    def test_all_runtime_paths_and_upload_fast_path_are_carried(self):
        text=(ROOT/'patches/gif-image2.patch').read_text()
        for path in ('Kernel/Stubs/GS.cpp','gs/gs_frontend.cpp','gs/ps2_gif_arbiter.cpp',
                     'ps2_vif1_interpreter.cpp','vu/ps2_vu1_impl.inl'):
            self.assertIn('+++ b/ps2xRuntime/src/lib/'+path,text)
        added='\n'.join(line[1:] for line in text.splitlines() if line.startswith('+') and not line.startswith('+++'))
        self.assertIn('flag < 2u',added)
        self.assertIn('imageFlg != GIF_FMT_IMAGE && imageFlg != GIF_FMT_DISABLED',added)
        self.assertIn('format == 2u || format == 3u',added)
        self.assertIn('flg == kGifFmtImage || flg == 3u',added)
        self.assertIn('flg == 2u || flg == 3u',added)
        # An executable asset-free runtime test accompanies the transport fixes.
        self.assertIn('referenceAddrPSMCT32',added)
        self.assertIn('gs.processGIFPacket',added)
        self.assertIn('vram[off + c] != payload[pixelIndex * 4u + c]',added)

    def test_patch_is_ordered_and_packaged(self):
        self.assertLess(native.RUNTIME_PATCHES.index('patches/runtime-diagnostics.patch'), native.RUNTIME_PATCHES.index('patches/gif-image2.patch'))
        self.assertIn('patches/gif-image2.patch',pipeline.SOURCE_FILES)
        self.assertIn('tests/test_image2_patch.py',pipeline.SOURCE_FILES)
