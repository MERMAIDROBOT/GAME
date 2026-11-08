"""Check branch-and-link reentry through the actual recompiler."""
from pathlib import Path
import re
import struct
import tempfile
import unittest

import behaviour_check
import overlays
import pipeline


class LinkBranchResume(unittest.TestCase):
    @unittest.skipUnless((pipeline.BUILD / 'ps2xRecomp/ps2_recomp').is_file(),
                         'Run the local compiler bootstrap to test real code generation')
    def test_regimm_link_returns_have_table_and_resume_labels(self):
        base = behaviour_check.CODE_BASE
        for form in (16, 17, 18, 19):
            with self.subTest(form=form), tempfile.TemporaryDirectory() as temp:
                # A conditional call with its return and target both inside the
                # function. The delay slot and continuation have different effects.
                word = 0x04800000 | (form << 16) | 3
                code = struct.pack('<8I', 0x27bdfff0, word, 0x24030001, 0x24420007,
                                   0x27bd0010, 0x24420002, 0x03e00008, 0)
                generated = behaviour_check.translate(Path(temp), code, pipeline)
                table = (generated / 'register_functions.cpp').read_text()
                target = base + 12
                owner = re.search(r'= (\w+); // 0x' + f'{target:x}' + r'\b', table)
                self.assertIsNotNone(owner, f'missing table slot 0x{target:x}')
                body = (generated / (owner[1] + '.cpp')).read_text()
                self.assertIn(f'case 0x{target:x}u: goto label_{target:x};', body)
                self.assertIn(f'label_{target:x}:', body)
                self.assertEqual(overlays.branch_handoff_hints(generated), [])
