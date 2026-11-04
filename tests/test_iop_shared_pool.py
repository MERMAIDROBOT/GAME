"""IRX images and AllocSysMemory share one first-fit IOP pool (patches/iop-shared-pool.patch). The old fixed
0x120000 heap floor reserved a module slice and left 832 KiB, starving a 1.1 MiB sound-driver buffer at boot."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / 'patches/iop-shared-pool.patch').read_text()


class Content(unittest.TestCase):
    def test_one_pool_for_modules_and_allocations(self):
        self.assertIn('+        static constexpr uint32_t HeapBase = 0x00010000u;', PATCH)
        self.assertIn('+            base = memory.allocate(span, 0x100u);', PATCH)
        self.assertIn('+                (void)memory.allocate(span, 16u, base);', PATCH)
        self.assertIn('tests/iop_shared_pool_test.cpp', PATCH)

    def test_registered_and_packaged(self):
        self.assertIn('patches/iop-shared-pool.patch', native.RUNTIME_PATCHES)
        for name in ('patches/iop-shared-pool.patch', 'tests/test_iop_shared_pool.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


def tool(name):
    local = pipeline.ENV / 'bin' / name
    return str(local) if local.exists() else shutil.which(name)


def _patched():
    header = pipeline.SOURCE / 'ps2xIOP/src/emulator/core/iop_memory.h'
    return header.is_file() and 'HeapBase = 0x00010000u' in header.read_text()


@unittest.skipUnless(_patched() and tool('cmake') and tool('ninja'), 'patched IOP source, cmake and ninja required')
class Behaviour(unittest.TestCase):
    def test_shared_pool_and_existing_iop_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pipeline.SOURCE / 'ps2xIOP'
            subprocess.run([tool('cmake'), '-S', str(src), '-B', tmp, '-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + tool('ninja'),
                            '-DPS2X_IOP_BUILD_TESTS=ON', '-DCMAKE_BUILD_TYPE=Release'], check=True, capture_output=True)
            targets = ['ps2_iop_shared_pool_test', 'ps2_iop_emulator_tests', 'ps2_iop_compatibility_tests']
            built = subprocess.run([tool('cmake'), '--build', tmp, '--target'] + targets, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout[-3000:])
            for target in targets:
                ran = subprocess.run([str(Path(tmp) / target)], capture_output=True, text=True, timeout=300)
                self.assertEqual(ran.returncode, 0, target + ': ' + (ran.stdout + ran.stderr)[-2000:])


if __name__ == '__main__':
    unittest.main()
