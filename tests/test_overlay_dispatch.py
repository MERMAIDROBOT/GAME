"""Compiled overlay dispatch policy and its runtime patch; no game or GPU needed."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'), 'C++ compiler required')
class OverlayDispatch(unittest.TestCase):
    def test_resident_overlay_wins_and_covers_its_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'overlays'
            built = subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++20', '-O2',
                                    '-I' + str(ROOT / 'runtime/overlays'), str(ROOT / 'tests/overlay_dispatch.cpp'),
                                    '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class OverlayDispatchPatch(unittest.TestCase):
    def test_patch_carries_the_reviewed_header_and_routes_both_lookups(self):
        patch = (ROOT / 'patches/overlay-dispatch.patch').read_text()
        header = (ROOT / 'runtime/overlays/overlay_dispatch.hpp').read_text()
        added = re.search(r'\+\+\+ b/ps2xRuntime/src/lib/overlay_dispatch\.hpp\n@@[^\n]*\n((?:\+[^\n]*\n)+)', patch)
        self.assertIsNotNone(added)
        self.assertEqual(''.join(line[1:] + '\n' for line in added.group(1).splitlines()), header)
        runtime = patch.split('+++ b/ps2xRuntime/src/lib/ps2_runtime.cpp', 1)[1]
        self.assertEqual(runtime.count('overlayRegistry().resolve('), 2)  # hasFunction and lookupFunction
        import native
        self.assertIn('patches/overlay-dispatch.patch', native.RUNTIME_PATCHES)


if __name__ == '__main__':
    unittest.main()
