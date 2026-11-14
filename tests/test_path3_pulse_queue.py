"""A masked PATH3 queue is released one GIF packet per MSKPATH3 pulse. The release used to concatenate the
whole remaining queue into a new buffer for every pulse, so a large masked upload cost O(bytes x packets):
in SSX 3 races that copy (and the malloc/madvise behind it) took about a quarter of the EE thread. The queue
is now consumed in place from a read head, and the GIF arbiter reuses drained packet buffers. The native test
(tests/runtime_timer.cpp, testMaskedPath3Pulses) checks one-packet-per-pulse release, packets spanning DMA
chunks, arbiter buffer reuse, and that 60000 pulses finish quickly (old code: about 2.4 s, new: about 1 ms)."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/path3-pulse-queue.patch'


class Content(unittest.TestCase):
    def test_release_in_place(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('+    size_t m_path3MaskedHead = 0;', text)
        self.assertIn('-    for (const auto &chunk : m_path3MaskedFifo)', text)
        self.assertIn('-        queued.insert(queued.end(), chunk.begin(), chunk.end());', text)
        self.assertIn('+    m_path3MaskedHead = offset;', text)
        self.assertIn('+    thread_local std::vector<std::vector<uint8_t>> spare;', text)
        runtime_test = (ROOT / 'tests/runtime_timer.cpp').read_text()
        self.assertIn('testMaskedPath3Pulses();', runtime_test)
        self.assertIn('queue copied per pulse', runtime_test)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        self.assertGreater(native.RUNTIME_PATCHES.index(PATCH),
                           native.RUNTIME_PATCHES.index('patches/runtime-title-conformance.patch'))
        for name in (PATCH, 'tests/test_path3_pulse_queue.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
