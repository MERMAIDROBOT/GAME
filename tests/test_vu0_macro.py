"""Synthetic VU0 macro-mode emission tests: no retail code."""
from pathlib import Path
import tempfile
import unittest
import knowledge
import vu0_macro

WORD = 0x4a00d839  # vcallmsr $vi27


def record(w, code): return f'// 0x1000: 0x{w:08x} label-not-trusted\nctx->pc = 0x1000u;\n' + code + '\n'


class VcallmsrRewriteTests(unittest.TestCase):
    def test_opcode_guard_and_unknown_emission_fail_closed(self):
        with self.assertRaises(ValueError): vu0_macro.check(WORD ^ 1)
        with self.assertRaises(ValueError): vu0_macro.rewrite(record(WORD, 'mystery();'))
        other = record(WORD ^ 1, vu0_macro.legacy(WORD))
        self.assertEqual(vu0_macro.rewrite(other), (other, 0, 0))

    def test_rewrite_reads_cmsar0_and_is_idempotent(self):
        # Pinned emitter spacing, exactly as it appears in translated sources.
        pinned = ('{     uint16_t instr_index = ctx->vi[27] & 0x1FF;     uint32_t target_byte_addr = '
                  '(uint32_t)instr_index << 3;     ctx->vu0_pc = target_byte_addr;     '
                  'runtime->vu0StartMicroProgram(rdram, ctx, target_byte_addr); }')
        updated, count, changed = vu0_macro.rewrite(record(WORD, pinned))
        self.assertEqual((count, changed), (1, 1))
        self.assertIn('ctx->vu0_cmsar0', updated)
        self.assertNotIn('ctx->vi[27]', updated)
        self.assertEqual(vu0_macro.rewrite(updated), (updated, 1, 0))

    def test_marker_without_opcode_is_rejected(self):
        with self.assertRaises(ValueError): vu0_macro.rewrite(vu0_macro.emit(WORD))

    def test_vi_index_lint(self):
        self.assertEqual(vu0_macro.vi_index_violations('ctx->vi[15] = 1; ctx->vi[0];'), [])
        self.assertEqual(vu0_macro.vi_index_violations('x = ctx->vi[27]; y = ctx->vi[16];'), [16, 27])

    def test_knowledge_corrects_vcallmsr_and_lint_blocks_other_out_of_range_vi(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a.cpp'; a.write_text(record(WORD, vu0_macro.legacy(WORD)))
            b = Path(tmp) / 'b.cpp'; b.write_text('SET_GPR_U32(ctx, 2, ctx->vi[20]);\n')
            old = a.read_bytes()
            with self.assertRaisesRegex(ValueError, 'past VI15'): knowledge.apply_translation(tmp)
            self.assertEqual(a.read_bytes(), old)  # nothing written when any file fails
            b.unlink()
            report = knowledge.apply_translation(tmp)
            self.assertEqual(report['totals']['corrected'], 1)
            self.assertEqual(vu0_macro.vi_index_violations(a.read_text()), [])
            self.assertEqual(knowledge.apply_translation(tmp)['totals']['corrected'], 0)


class Vu0MemoryRewriteTests(unittest.TestCase):
    VSQI = 0x4be1137d  # vsqi.xyzw vf2, (vi1++)
    VLQI = 0x4be10b7c  # vlqi.xyzw vf1, (vi1++)

    def test_store_uses_vu0_memory_and_correct_fields(self):
        updated, count, changed = vu0_macro.mem_rewrite(record(self.VSQI, vu0_macro.mem_legacy(self.VSQI)))
        self.assertEqual((count, changed), (1, 1))
        self.assertIn('runtime->memory().getVU0Data()', updated)
        self.assertIn('ctx->vu0_vf[2]', updated)          # VF is bits 15-11 for the store forms
        self.assertIn('(uint32_t(ctx->vi[1]) & 0xFFu) << 4', updated)
        self.assertNotIn('WRITE128', updated)
        self.assertEqual(vu0_macro.mem_rewrite(updated), (updated, 1, 0))

    def test_all_forms_and_partial_dest(self):
        for low, op in [(0x37c, 'vlqi'), (0x37d, 'vsqi'), (0x37e, 'vlqd'), (0x37f, 'vsqd'), (0x3fe, 'vilwr'), (0x3ff, 'viswr')]:
            word = 0x4a000000 | (0b1010 << 21) | (3 << 16) | (4 << 11) | low  # .xz, rt=3, rd=4
            self.assertEqual(vu0_macro.mem_fields(word)[0], op)
            updated, count, changed = vu0_macro.mem_rewrite(record(word, vu0_macro.mem_legacy(word)))
            self.assertEqual((count, changed), (1, 1), op)
            self.assertNotIn('READ128', updated); self.assertNotIn('WRITE32', updated)
        store = vu0_macro.mem_emit(0x4a000000 | (0b1010 << 21) | (3 << 16) | (4 << 11) | 0x37d)
        self.assertIn('ps2_off + 0u', store); self.assertIn('ps2_off + 8u', store)
        self.assertNotIn('ps2_off + 4u', store); self.assertNotIn('ps2_off + 12u', store)

    def test_unknown_emission_and_orphan_marker_fail_closed(self):
        with self.assertRaises(ValueError): vu0_macro.mem_rewrite(record(self.VLQI, 'mystery();'))
        with self.assertRaises(ValueError): vu0_macro.mem_rewrite(vu0_macro.mem_emit(self.VLQI))

    def test_knowledge_bank_applies_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a.cpp'; a.write_text(record(self.VSQI, vu0_macro.mem_legacy(self.VSQI)))
            report = knowledge.apply_translation(tmp, only={'vu0-macro-vu-memory'})
            self.assertEqual(report['totals']['corrected'], 1)
            self.assertIn(vu0_macro.MEM_MARKER, a.read_text())


if __name__ == '__main__':
    unittest.main()
