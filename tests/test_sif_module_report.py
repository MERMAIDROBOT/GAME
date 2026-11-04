"""sceSifLoadModuleBuffer reports each load (first 64) in release builds, where RUNTIME_LOG is compiled out: whether
an IOP module loaded decides which RPC services exist, and a silent failure looks like a hang at a bind loop."""
from pathlib import Path
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/sif-module-load-report.patch'


class Content(unittest.TestCase):
    def test_reported(self):
        text = (ROOT / PATCH).read_text()
        self.assertIn('[SIF module] load-buffer addr=0x%x tag=%s handled=%d id=%d', text)
        self.assertIn('reported.fetch_add(1) < 64u', text)

    def test_registered_and_packaged(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        for name in (PATCH, 'tests/test_sif_module_report.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
