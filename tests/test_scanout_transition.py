"""When a game switches display mode (512-wide game to 640-wide movie) the renderer can still hold only the old
512-wide picture; stretching it to the new width showed a brief zoom before videos. The presenter keeps the previous
picture until a target that covers the display exists."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import pipeline

ROOT = Path(__file__).resolve().parents[1]


class Content(unittest.TestCase):
    def test_presenter_holds(self):
        owner = (ROOT / 'runtime/gpu/native-plugin/hw_owner.hpp').read_text()
        self.assertIn('if (!coversDisplay(w, unsigned(fb.DBX), gw, s) && !last.bytes().empty())', owner)
        for name in ('tests/scanout_transition.cpp', 'tests/test_scanout_transition.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Behaviour(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'scanout_transition'
            subprocess.run(['clang++', '-std=c++17', str(ROOT / 'tests/scanout_transition.cpp'), '-o', str(exe)], check=True)
            ran = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == '__main__':
    unittest.main()
