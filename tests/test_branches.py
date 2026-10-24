import tempfile
from pathlib import Path
import unittest
import branches
import knowledge


class SignedBranches(unittest.TestCase):
    def test_every_branch_kind_register_and_idempotence(self):
        for rs in range(32):
            cases = [(op,0,compare) for op,compare in ((6,'<='),(7,'>'),(22,'<='),(23,'>'))]
            cases += [(1,rt,'<' if rt%2==0 else '>=') for rt in (0,1,2,3,16,17,18,19)]
            for op,rt,compare in cases:
                word=op<<26 | rs<<21 | rt<<16 | 1
                text=f'// 0x1000: 0x{word:08x} ignored mnemonic\nconst bool take=(GPR_S32(ctx, {rs}) {compare} 0);\n'
                updated,n,changes=branches.rewrite(text)
                self.assertEqual((n,changes),(1,1));self.assertIn('GPR_S64',updated)
                self.assertEqual(branches.rewrite(updated),(updated,1,0))

    def test_wrong_register_operator_or_unknown_emission_rejected(self):
        for body in ('GPR_S32(ctx, 9) > 0','GPR_S32(ctx, 8) < 0','unknown()'):
            with self.assertRaises(ValueError):branches.rewrite('// 0x1000: 0x1d000001 bgtz\n'+body)

    def test_normal_word_arithmetic_is_untouched(self):
        text='// 0x1000: 0x25080001 addiu\nSET_GPR_S32(ctx,8,1);\n'
        self.assertEqual(branches.rewrite(text),(text,0,0))

    def test_translation_pipeline_corrects_and_rejects_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a.cpp'
            old='// 0x1000: 0x1d000001 bgtz\nconst bool take=(GPR_S32(ctx, 8) > 0);\n'
            path.write_text(old)
            bad=Path(tmp)/'b.cpp';bad.write_text('// 0x1000: 0x1d000001 bgtz\nunknown();\n')
            with self.assertRaises(ValueError):knowledge.apply_translation(tmp)
            self.assertEqual(path.read_text(),old)
            bad.unlink();report=knowledge.apply_translation(tmp)
            self.assertEqual(report['totals']['corrected'],1)
            self.assertIn('S64',path.read_text())

    def test_zero_extended_vu_sign_is_positive_64_bit(self):
        rows=branches.qualify()
        self.assertTrue(next(r['expected'] for r in rows if r['value']=='0000000080000000' and r['operator']=='>'))
        self.assertFalse(next(r['expected'] for r in rows if r['value']=='ffffffffffffffff' and r['operator']=='>'))
