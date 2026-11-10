"""F12 saves the picture about to be presented as a PNG in ~/Pictures/PS2 Port Lab Screenshots. Raylib's own F12
writes into the working directory, which is "/" for an app started from Finder, and only logs a failure. The picture
is drawn through raylib's batch, which reaches the back buffer at EndDrawing: the batch is flushed before the grab
(without that every capture was black). PS2_PORT_SCREENSHOT_AT=<seconds> requests one for tests without a keyboard."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/screenshot-key.patch'


class Content(unittest.TestCase):
    def test_key_request_and_flushed_capture(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('+        if (key == KEY_F12) screenshotRequested = true;', text)
        self.assertIn('+bool ps2PortConsumeScreenshotRequest()', text)
        flush = text.index('+    rlDrawRenderBatchActive();')
        self.assertLess(flush, text.index('+    Image picture = LoadImageFromScreen();'))
        self.assertIn('PS2 Port Lab Screenshots', text)
        self.assertIn('PS2_PORT_SCREENSHOT_AT', text)
        self.assertIn('F12', (ROOT / 'CONTROLS.md').read_text())

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_screenshot_key.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
