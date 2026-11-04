"""Compiled stub-interior entry rule and the translator patch that applies it; no game needed."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'), 'C++ compiler required')
class StubInteriorEntries(unittest.TestCase):
    def test_jumps_into_a_stubbed_extent_become_guest_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'stub_interior'
            built = subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++20', '-O2',
                                    '-I' + str(ROOT / 'tools/recomp'), str(ROOT / 'tests/stub_interior_entries.cpp'),
                                    '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class StubInteriorEntriesPatch(unittest.TestCase):
    def test_patch_carries_the_reviewed_header_and_seeds_guest_entries(self):
        patch = (ROOT / 'patches/stub-interior-entries.patch').read_text()
        header = (ROOT / 'tools/recomp/stub_interior_entries.h').read_text()
        added = re.search(r'\+\+\+ b/ps2xRecomp/include/ps2recomp/stub_interior_entries\.h\n@@[^\n]*\n((?:\+[^\n]*\n)+)', patch)
        self.assertIsNotNone(added)
        self.assertEqual(''.join(line[1:] + '\n' for line in added.group(1).splitlines()), header)
        recompiler = patch.split('+++ b/ps2xRecomp/src/lib/ps2_recompiler.cpp', 1)[1]
        self.assertIn('+        guestFallbackEntryAddresses.insert(stubInteriorTargets.begin(), stubInteriorTargets.end());',
                      recompiler)
        import native
        self.assertIn('patches/stub-interior-entries.patch', native.RUNTIME_PATCHES)


if __name__ == '__main__':
    unittest.main()
