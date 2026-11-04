"""VU learning: packaged apps record each VU program the game runs (launcher VuCaptureDirectory ->
PS2X_VU1_CAPTURE); the next build-native merges recordings whose bytes hash to their runtime-given names and
compiles the grown set. Synthetic images, no game data."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import native
import pipeline
import vectors

ROOT = Path(__file__).resolve().parents[1]


def image(seed, size=16384):
    data = bytes((seed * 31 + i * 7) & 0xff for i in range(size))
    unit = 'vu0' if size == 4096 else 'vu1'
    return f'{unit}_{vectors.fnv1a64(data):016x}.bin', data


class Learning(unittest.TestCase):
    def test_fnv_matches_runtime(self):
        self.assertEqual(vectors.fnv1a64(b''), 0xcbf29ce484222325)
        self.assertEqual(vectors.fnv1a64(b'a'), 0xaf63dc4c8601ec8c)

    def test_merge_verifies_and_grows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, private = Path(tmp) / 'home', Path(tmp) / 'private'
            app = home / 'Library/Application Support/PS2 Port Lab/title/vu-capture'
            app.mkdir(parents=True); (private / 'vu-capture-old').mkdir(parents=True)
            good, data = image(1); (private / 'vu-capture-old' / good).write_bytes(data)
            second, data2 = image(2); (app / second).write_bytes(data2)
            vu0, data0 = image(3, 4096); (app / vu0).write_bytes(data0)
            wrong, _ = image(4); (app / wrong).write_bytes(data)                       # name/bytes disagree
            short, data5 = image(5); (app / short).write_bytes(data5[:100])           # incomplete image
            with mock.patch.object(Path, 'home', return_value=home):
                self.assertEqual(vectors.learn({'id': 'title'}, private), 3)
                self.assertEqual(vectors.learn({'id': 'title'}, private), 0)           # nothing new
                self.assertIsNone(vectors.refresh_compiled({'id': 'title'}, private))  # no compiler built yet
            self.assertEqual(sorted(p.name for p in (private / 'vu-capture').iterdir()), sorted([good, second, vu0]))

    def test_wiring(self):
        source = (ROOT / 'native.py').read_text()
        self.assertIn("config['VuCaptureDirectory'] = str(storage / 'vu-capture')", source)
        self.assertIn('if refresh_compiled(profile, private) is not None:', source)
        launcher = (ROOT / 'cmake/LocalLauncher.m').read_text()
        self.assertLess(launcher.index('unsetenv("PS2X_VU1_CAPTURE");'),
                        launcher.index('setenv("PS2X_VU1_CAPTURE", vuCapture.fileSystemRepresentation, 1);'))
        self.assertIn('tests/test_vu_learning.py', pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
