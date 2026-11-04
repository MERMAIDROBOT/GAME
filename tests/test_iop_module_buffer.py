"""sceSifLoadModuleBuffer takes an IOP address: titles SIF-DMA each IRX into IOP RAM, then load it from there. The
emulator read the image from EE memory, so every such load failed and the title waited forever at a bind loop for
the services those modules provide. Images placed in EE memory still load (fallback)."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/iop-module-buffer.patch'


class Content(unittest.TestCase):
    def test_reads_iop_ram_first(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('IopModuleLoader::readElfFromIop(memory, guestAddress, image) &&', text)
        self.assertIn('!IopModuleLoader::readElfFromGuest(host, guestAddress, image))', text)
        self.assertIn('testModuleBufferFromIopRam', text)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_iop_module_buffer.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


def tool(name):
    local = pipeline.ENV / 'bin' / name
    return str(local) if local.exists() else shutil.which(name)


@unittest.skipUnless('readElfFromIop' in (pipeline.SOURCE / 'ps2xIOP/src/emulator/iop_emulator.cpp').read_text()
                     if (pipeline.SOURCE / 'ps2xIOP/src/emulator/iop_emulator.cpp').is_file() else False,
                     'patched IOP source required')
class Behaviour(unittest.TestCase):
    def test_module_from_iop_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([tool('cmake'), '-S', str(pipeline.SOURCE / 'ps2xIOP'), '-B', tmp, '-G', 'Ninja',
                            '-DCMAKE_MAKE_PROGRAM=' + tool('ninja'), '-DPS2X_IOP_BUILD_TESTS=ON',
                            '-DCMAKE_BUILD_TYPE=Release'], check=True, capture_output=True)
            built = subprocess.run([tool('cmake'), '--build', tmp, '--target', 'ps2_iop_blocking_rpc_test'],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout[-3000:])
            ran = subprocess.run([str(Path(tmp) / 'ps2_iop_blocking_rpc_test')], capture_output=True, text=True, timeout=300)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
