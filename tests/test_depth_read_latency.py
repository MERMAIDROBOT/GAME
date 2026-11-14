"""Small depth reads no longer stop the CPU behind the GPU.

SSX 3 reads 16x8 of its Z24 depth buffer at each sun and light, every frame, to size the glare. Two things made each
read wait for everything the GPU had queued (7-12 ms), and a race fell to ~30 frames a second wherever a light was
in view (27% of fields dropped on the timed route, 30 s of CPU waits in 75 s):

1. Writing TRXDIR=1 made the GS model sync the rectangle's whole rows (256-512 blocks) before the reader ran;
   hwgs_read_fifo syncs exactly the blocks it reads, so the plugin turns the row sync off (exact_read_sync).
2. PS2_PORT_HW_GS_ZREAD_LATENCY=1 (a title's profile opts in) answers a small depth read the GPU holds from the
   GPU's copy of that depth buffer made at the same point of an earlier frame (MemoryReadAsync: its own command
   buffer and fence, nothing waiting). Dropped fields 27% -> 1.3%, CPU waits 30 s -> 0.3 s.
3. A CLUT load synced eight blocks from CBP whatever the CLUT's size (ClutSync below).
"""
from pathlib import Path
import re
import unittest

import launch_config
import pipeline

ROOT = Path(__file__).resolve().parents[1]
GPU = ROOT / 'runtime/gpu'


class ExactReadSync(unittest.TestCase):
    def test_model_skips_row_sync_for_reads_only(self):
        text = (GPU / 'host-model/src/gs.cpp').read_text()
        self.assertIn('if ((trxdir_ == 1 && !exact_read_sync) || trxdir_ == 2)       // out of it', text)
        # writes into local memory still sync as before
        self.assertIn('if (trxdir_ == 0 || trxdir_ == 2)       // into local memory', text)
        self.assertIn('bool exact_read_sync = false;', (GPU / 'host-model/include/ps2host/gs.h').read_text())

    def test_plugin_turns_it_on_and_syncs_exact_blocks(self):
        text = (GPU / 'host-adapter/hwgs_capi.cpp').read_text()
        self.assertIn('gs::TheGs().exact_read_sync = exact;', text)
        self.assertIn('PS2_PORT_HW_GS_EXACT_READ', text)
        body = text[text.index('size_t hwgs_read_fifo('):]
        body = body[:body.index('\n}\n')]
        self.assertIn('g.on_memory(blocks[first], blocks[next - 1] + 1, false);', body)
        self.assertIn('const gs::Memory& source = latent ? *latent : g.memory;', body)
        self.assertIn('source.Read(sbp, sbw, sx + x, sy + y, spsm)', body)


class ClutSync(unittest.TestCase):
    """A CLUT load syncs the blocks the CLUT is read from (4 for eight-bit, 1 for four-bit, 8 for CSM2), as the
    bridge's LoadClut checks them - not eight for every load: SSX 3's four-bit CLUT at CBP 0 brought back the frame
    buffer's first blocks ~50 times a second, each waiting for the GPU."""
    def test_clut_blocks(self):
        text = (GPU / 'host-model/src/gs.cpp').read_text()
        self.assertIn('const u32 clut_blocks = Bits(tex0, 55, 1) ? 8u : eight ? 4u : 1u;', text)
        self.assertIn('if (on_memory) on_memory(cbp, cbp + clut_blocks, false);', text)
        self.assertNotIn('on_memory(cbp, cbp + 8, false)', text)
        bridge = (GPU / 'host-adapter/gpu_bridge.cpp').read_text()
        self.assertIn('const u32 clut_blocks = (psm == gs::PSMT8 || psm == gs::PSMT8H) ? 4u : 1u;', bridge)


class LatentDepthRead(unittest.TestCase):
    def test_bridge_conditions(self):
        text = (GPU / 'host-adapter/gpu_bridge.cpp').read_text()
        start = text.index('const gs::Memory* LatentDepthRead(')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('PS2_PORT_HW_GS_ZREAD_LATENCY', body)
        self.assertIn('spsm != gs::PSMZ24 && spsm != gs::PSMZ32', body)
        self.assertIn('w * h > 64 * 64', body)
        self.assertIn('FlushPendingClear(0);', body)
        self.assertIn('!GpuHolds(b, first, last)', body)
        # one copy started a frame, after the depth buffer is written back to GPU memory
        self.assertTrue(re.search(r'if \(b\.frame != requested_frame\).*WriteBack\(b, o\).*MemoryReadAsync\(copy_begin, copy_end, \+\+serial',
                                  body, re.S))
        # blocks the CPU holds newer come from the CPU, the rest from the GPU's copy
        self.assertIn('b.blocks[i] == Bridge::kGpuNewer', body)

    def test_renderer_reads_without_waiting(self):
        text = (GPU / 'src/gs_renderer.cpp').read_text()
        start = text.index('bool VulkanGsRenderer::MemoryReadAsync(')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('vkGetFenceStatus', body)
        self.assertIn('vkCmdCopyBuffer(slot->cmd, gsmem_, slot->buffer, 1, &copy);', body)
        self.assertNotIn('g_.Run(', body)
        self.assertIn('for (AsyncRead* list : {async_, mem_reads_})', text)   # freed with the renderer
        self.assertIn('return inner_->MemoryReadAsync(', (GPU / 'src/threaded_renderer.cpp').read_text())
        self.assertIn('virtual bool MemoryReadAsync(', (GPU / 'include/ps2gpu/gs_renderer.h').read_text())

    def test_declared_and_registered(self):
        self.assertIn('PS2_PORT_HW_GS_ZREAD_LATENCY', launch_config.BOOLS)
        self.assertIn('tests/test_depth_read_latency.py', pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
