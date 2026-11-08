from pathlib import Path
import tempfile
import unittest
from indirect_entries import audit


class IndirectEntries(unittest.TestCase):
    def test_known_table_does_not_hide_unregistered_decoded_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            table=root/'o01_register_functions.cpp'
            table.write_text('table[0] = f; // 0x1000\n')
            (root/'f.cpp').write_text('// 0x1000: 0x00800008 jr $a0\n// 0x1004: 0x24020016 addiu\n')
            report=audit(root)
            self.assertFalse(report['passed'])
            self.assertEqual(report['findings'][0]['missing_sample'],['00001004'])
            table.write_text(table.read_text()+'table[1] = f; // 0x1004\n')
            self.assertTrue(audit(root)['passed'])

    def test_normal_return_and_unreferenced_data_do_not_require_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'register_functions.cpp').write_text('table[0] = f; // 0x1000\n')
            (root/'f.cpp').write_text('// 0x1000: 0x03e00008 jr $ra\n// 0x1004: 0x00000000 nop\n')
            self.assertEqual(audit(root)['functions_checked'],0)
