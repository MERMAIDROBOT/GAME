"""Each rendering pass sets its own draw state: none is taken as carried over from the pass before.

The renderer keeps what it has set in a command buffer (pipeline, descriptor sets, viewport, scissor, depth and
stencil state, vertex buffer) and sets only what changes. MoltenVK gives each rendering pass a Metal render encoder
of its own and carries that state to the next one only in part, so a pass begun after a copy or a buffer update
between passes could draw with state the cache took to be set and the GPU did not have. SSX 3 lost the end of a
race frame - its glare, colour grading and HUD - 100 to 200 frames a minute in some runs (the HUD bars and the sun
"flashing"): GPU snapshots of the frame buffer showed it unchanged from the glare pass to the frame's end although
every batch was recorded. Whether a compute dispatch (which resets the cache) fell between the passes decided it,
so whole runs came out good or bad. With the cache reset at each pass: 0-0.7 a minute in four runs.
"""
from pathlib import Path
import unittest

import pipeline

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / 'runtime/gpu/src/gs_renderer.cpp'


class PassState(unittest.TestCase):
    def test_draw_pass_resets_the_cache(self):
        text = RENDERER.read_text()
        begin = text.index('            ++g_passes;\n            vkCmdBeginRendering(cmd, &ri);')
        block = text[begin:text.index('\n            }\n', begin)]
        self.assertIn('drawn_ = DrawState{g_.recording_id()};', block)
        # and the batch's state is set after it, in the same recording
        self.assertLess(begin, text.index('if (d.depth_test != depth_test) vkCmdSetDepthTestEnable', begin))

    def test_other_passes_reset_it(self):
        text = RENDERER.read_text()
        for function in ('void VulkanGsRenderer::DepthIn(', 'void VulkanGsRenderer::DateSetup('):
            start = text.index(function)
            self.assertIn('drawn_ = DrawState{}', text[start:text.index('\n}\n', start)])

    def test_registered(self):
        self.assertIn('tests/test_pass_state.py', pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
