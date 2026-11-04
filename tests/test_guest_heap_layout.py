"""A title that hands SetupHeap an explicit heap above the default heap end (a small C-library heap just under its
own stack, managing the RAM below itself) gets exactly that range for malloc; the runtime must not move the heap to
the first free RAM after the executable, where the title keeps its own data."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/guest-heap-exact.patch'


class Content(unittest.TestCase):
    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/guest_heap_layout.cpp', 'tests/test_guest_heap_layout.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)

    def test_setup_heap_uses_the_rule(self):
        patch = (ROOT / PATCH).read_text()
        for fragment in ('GuestHeapLayout::exactLimit(heapBase, heapSize, g_mainThreadStackBase, PS2_RAM_SIZE)',
                         'runtime->configureExactGuestHeap(heapBase, exact)',
                         'SetupHeap keeps an explicit heap placed above the default heap end'):
            self.assertIn(fragment, patch)


def _patched():
    return (pipeline.SOURCE / 'ps2xRuntime/include/runtime/guest_heap_layout.h').is_file()


@unittest.skipUnless(_patched() and shutil.which('clang++'), 'patched runtime source and clang++ required')
class Behaviour(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'guest_heap_layout'
            subprocess.run(['clang++', '-std=c++17', '-I', str(pipeline.SOURCE / 'ps2xRuntime/include'),
                            str(ROOT / 'tests/guest_heap_layout.cpp'), '-o', str(exe)], check=True)
            ran = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == '__main__':
    unittest.main()
