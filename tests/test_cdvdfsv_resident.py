"""The CD/DVD file server's disk-ready RPC (SID 0x8000059A) is resident from boot, as cdvdfsv is in a console's
ROM. Stripped titles keep their own CD library, which the recompiler cannot name and replace; its sceCdDiskReady
binds this SID and retries forever while no server answers. A title-loaded cdvdfsv still wins (emulated servers
answer first). Behaviour runs in ps2xIOP's compatibility tests (built by tests/test_iop_shared_pool.py)."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/cdvdfsv-resident.patch'


class Content(unittest.TestCase):
    def test_service_and_test_in_patch(self):
        text = (ROOT / PATCH).read_text()
        for fragment in ('constexpr uint32_t kDiskReadySid = 0x8000059Au;', 'createCdvdfsvService(host)',
                         'src/modules/cdvdfsv.cpp', '"Resident CDVDFSV disk-ready RPC", cdvdfsvDiskReady'):
            self.assertIn(fragment, text)
        self.assertNotIn('moduleAliases', text.split('class CdvdfsvService')[1].split('handleRpc')[0])

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_cdvdfsv_resident.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
