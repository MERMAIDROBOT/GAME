"""sceMpegAddStrCallback stream types follow the SDK (M2V 0, IPU 1, PCM 2, ADPCM 3). The HLE numbered ADPCM 2, so a
title registering its movie audio as ADPCM (3) received no audio, its player waited for an audio fill level that never
came, and the movie never started. Audio packets go to PCM and ADPCM callbacks; PCM (2) titles are unchanged."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/mpeg-stream-types.patch'


class Content(unittest.TestCase):
    def test_sdk_numbering(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('+        constexpr uint32_t kMpegStrPCM = 2u;', text)
        self.assertIn('+        constexpr uint32_t kMpegStrADPCM = 3u;', text)
        self.assertIn('registerStreamCallback(3u, kAudioUserData + 1u)', text)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_mpeg_stream_types.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
