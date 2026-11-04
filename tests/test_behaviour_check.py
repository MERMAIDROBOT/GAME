"""Reference model, case layout and harness I/O of the instruction behaviour check; no game needed."""
import random
import struct
import unittest

import behaviour_check as bc


def state(**regs):
    gpr = [[0, 0] for _ in range(32)]
    for name, value in regs.items():
        gpr[int(name[1:])][0] = value & bc.M64
    return bc.State(gpr, 0, 0, 0, 0, bytes(range(bc.DATA_BYTES)))


def run(name, s, **fields):
    return bc.expected(name, dict(dict(rs=0, rt=0, rd=0, sa=0, imm=0), **fields), s)


class Model(unittest.TestCase):
    def test_32_bit_results_are_sign_extended_and_upper_halves_kept(self):
        s = state(r1=0x7FFFFFFF, r2=1)
        s.gpr[3][1] = 0x1234
        out = run('addu', s, rs=1, rt=2, rd=3)
        self.assertEqual(out.gpr[3], [0xFFFFFFFF80000000, 0x1234])
        self.assertEqual(run('srl', state(r1=0xFFFFFFFF80000000), rt=1, rd=2, sa=0).get(2), 0xFFFFFFFF80000000)
        self.assertEqual(run('srl', state(r1=0xFFFFFFFF80000000), rt=1, rd=2, sa=1).get(2), 0x40000000)

    def test_overflow_inputs_are_excluded(self):
        with self.assertRaises(bc.Trap):
            run('add', state(r1=0x7FFFFFFF, r2=1), rs=1, rt=2, rd=3)
        with self.assertRaises(bc.Trap):
            run('daddi', state(r1=0x7FFFFFFFFFFFFFFF), rs=1, rt=2, imm=1)

    def test_writes_to_zero_are_discarded(self):
        self.assertEqual(run('addiu', state(r1=5), rs=1, rt=0, imm=1).gpr[0], [0, 0])

    def test_multiply_writes_rd_and_both_halves(self):
        out = run('mult', state(r1=-2, r2=3), rs=1, rt=2, rd=4)
        self.assertEqual((out.lo, out.hi, out.get(4)), (bc.M64 - 5, bc.M64, bc.M64 - 5))
        out = run('mult1', state(r1=-2, r2=3), rs=1, rt=2, rd=4)
        self.assertEqual((out.lo1, out.hi1, out.lo, out.hi), (bc.M64 - 5, bc.M64, 0, 0))
        s = state(r1=2, r2=3)
        s.lo, s.hi = 0xFFFFFFFF, 0
        out = run('maddu', s, rs=1, rt=2, rd=5)
        self.assertEqual((out.lo, out.hi), (5, 1))

    def test_divide_edge_cases_follow_the_ee(self):
        out = run('div', state(r1=7, r2=0), rs=1, rt=2)
        self.assertEqual((out.lo, out.hi), (bc.M64, 7))
        out = run('div', state(r1=-7, r2=0), rs=1, rt=2)
        self.assertEqual((out.lo, out.hi), (1, bc.M64 - 6))
        out = run('div', state(r1=0xFFFFFFFF80000000, r2=bc.M64), rs=1, rt=2)
        self.assertEqual((out.lo, out.hi), (0xFFFFFFFF80000000, 0))
        out = run('div', state(r1=-7, r2=2), rs=1, rt=2)
        self.assertEqual((out.lo, out.hi), (bc.M64 - 2, bc.M64))  # truncates toward zero
        out = run('divu', state(r1=7, r2=0), rs=1, rt=2)
        self.assertEqual((out.lo, out.hi), (bc.M64, 7))

    def test_left_right_pairs_load_and_store_an_unaligned_word(self):
        base = bc.DATA_BASE + 0x21
        s = state(r1=base, r2=0)
        s = run('lwr', s, rs=1, rt=2, imm=0)
        s = run('lwl', s, rs=1, rt=2, imm=3)
        self.assertEqual(s.get(2), bc.sext(int.from_bytes(bytes(range(0x21, 0x25)), 'little'), 32) & bc.M64)
        s = run('ldr', s, rs=1, rt=3, imm=0)
        s = run('ldl', s, rs=1, rt=3, imm=7)
        self.assertEqual(s.get(3), int.from_bytes(bytes(range(0x21, 0x29)), 'little'))
        s = state(r1=base, r2=0x1122334455667788)
        s = run('sdr', s, rs=1, rt=2, imm=0)
        s = run('sdl', s, rs=1, rt=2, imm=7)
        self.assertEqual(s.load(base, 8), 0x1122334455667788)
        self.assertEqual(s.byte(base - 1), 0x20)

    def test_partial_lwr_keeps_the_upper_word(self):
        s = state(r1=bc.DATA_BASE + 0x11, r2=0xAAAAAAAABBBBBBBB)
        self.assertEqual(run('lwr', s, rs=1, rt=2).get(2), 0xAAAAAAAABB131211)

    def test_quadword_accesses_ignore_the_low_address_bits(self):
        s = state(r1=bc.DATA_BASE + 0x1F)
        out = run('lq', s, rs=1, rt=2)
        self.assertEqual(out.gpr[2], [s.load(bc.DATA_BASE + 0x10, 8), s.load(bc.DATA_BASE + 0x18, 8)])

    def test_likely_branches_nullify_the_delay_slot_only_when_not_taken(self):
        fields = dict(rs=1, rt=2)
        taken, skipped = bc.BRANCH_TAKEN_REG, bc.BRANCH_SKIPPED_REG
        for name, a, b, delay, fallthrough in (('beq', 1, 1, 1, 0), ('beq', 1, 2, 1, 1),
                                               ('beql', 1, 1, 1, 0), ('beql', 1, 2, 0, 1),
                                               ('bltz', bc.M64, 0, 1, 0), ('bgezl', bc.M64, 0, 0, 1)):
            out = bc.expected(name, fields, state(r1=a, r2=b))
            self.assertEqual((out.get(taken), out.get(skipped)), (delay, fallthrough), name)

    def test_conditional_moves_keep_the_upper_half(self):
        s = state(r1=0x1111, r2=0)
        s.gpr[1][1], s.gpr[3][1] = 0xAAAA, 0xBBBB
        self.assertEqual(run('movz', s, rs=1, rt=2, rd=3).gpr[3], [0x1111, 0xBBBB])


def quad(**regs):
    s = state()
    for name, value in regs.items():
        bc.setq(s, int(name[1:]), value)
    return s


def words(*values):
    return bc.join(list(values), 32)


class Multimedia(unittest.TestCase):
    def test_interleave_and_pack_lane_order(self):
        s = quad(r1=words(0xA0, 0xA1, 0xA2, 0xA3), r2=words(0xB0, 0xB1, 0xB2, 0xB3))
        self.assertEqual(bc.q(run('pextlw', s, rs=1, rt=2, rd=3), 3), words(0xB0, 0xA0, 0xB1, 0xA1))
        self.assertEqual(bc.q(run('pextuw', s, rs=1, rt=2, rd=3), 3), words(0xB2, 0xA2, 0xB3, 0xA3))
        self.assertEqual(bc.q(run('ppacw', s, rs=1, rt=2, rd=3), 3), words(0xB0, 0xB2, 0xA0, 0xA2))
        self.assertEqual(bc.q(run('pcpyld', s, rs=1, rt=2, rd=3), 3), words(0xB0, 0xB1, 0xA0, 0xA1))
        self.assertEqual(bc.q(run('pcpyud', s, rs=1, rt=2, rd=3), 3), words(0xA2, 0xA3, 0xB2, 0xB3))
        self.assertEqual(bc.q(run('prot3w', s, rt=2, rd=3), 3), words(0xB1, 0xB2, 0xB0, 0xB3))

    def test_saturation_and_absolute_limits(self):
        s = quad(r1=words(0x7FFFFFFF, 0x80000000, 5, 0xFFFFFFFF), r2=words(1, 0xFFFFFFFF, 7, 1))
        self.assertEqual(bc.q(run('paddsw', s, rs=1, rt=2, rd=3), 3), words(0x7FFFFFFF, 0x80000000, 12, 0))
        self.assertEqual(bc.q(run('padduw', s, rs=1, rt=2, rd=3), 3), words(0x80000000, 0xFFFFFFFF, 12, 0xFFFFFFFF))
        self.assertEqual(bc.q(run('psubuw', s, rs=1, rt=2, rd=3), 3), words(0x7FFFFFFE, 0, 0, 0xFFFFFFFE))
        self.assertEqual(bc.q(run('pabsw', s, rt=1, rd=3), 3), words(0x7FFFFFFF, 0x7FFFFFFF, 5, 1))

    def test_halfword_multiply_layout_and_horizontal_odd_words(self):
        s = quad(r1=bc.join(list(range(1, 9)), 16), r2=bc.join([2] * 8, 16))
        out = run('pmulth', s, rs=1, rt=2, rd=3)
        self.assertEqual(bc.lanes(bc._hilo(out)[1], 32), [2, 4, 10, 12])
        self.assertEqual(bc.lanes(bc._hilo(out)[0], 32), [6, 8, 14, 16])
        self.assertEqual(bc.q(out, 3), words(2, 6, 10, 14))
        out = run('phmsbh', s, rs=1, rt=2, rd=3)
        self.assertEqual(bc.q(out, 3), words(2, 2, 2, 2))
        self.assertEqual(bc.lanes(bc._hilo(out)[1], 32)[1], ~4 & bc.M32)

    def test_word_multiply_writes_full_products_and_split_hi_lo(self):
        s = quad(r1=words(0x80000000, 0, 3, 0), r2=words(2, 0, 0xFFFFFFFF, 0))
        out = run('pmultw', s, rs=1, rt=2, rd=3)
        self.assertEqual(out.gpr[3], [0xFFFFFFFF00000000, bc.M64 - 2])
        self.assertEqual((out.lo, out.hi, out.lo1, out.hi1), (0, bc.M64, bc.M64 - 2, bc.M64))

    def test_saturating_move_from_hi_lo(self):
        s = state()
        s.hi, s.lo, s.hi1, s.lo1 = 1, 0, 0xFFFFFFFF, 0x12345678
        self.assertEqual(run('pmfhl.slw', s, rd=3).gpr[3], [0x7FFFFFFF, 0xFFFFFFFF80000000])
        s.hi1 = 0
        self.assertEqual(run('pmfhl.slw', s, rd=3).gpr[3][1], 0x12345678)

    def test_funnel_shift_by_the_sa_register(self):
        s = quad(r1=(1 << 128) - 1, r2=0)
        self.assertEqual(bc.q(bc.expected('mtsab+qfsrv', dict(rs=1, rt=2, rd=3, imm=3), s), 3), ((1 << 24) - 1) << 104)
        self.assertEqual(bc.q(bc.expected('mtsah+qfsrv', dict(rs=1, rt=2, rd=3, imm=8 + 1), s), 3), ((1 << 16) - 1) << 112)


import shutil


@unittest.skipUnless(shutil.which('cc') or shutil.which('clang') or shutil.which('gcc'), 'C compiler required for ps2float')
class FloatingPoint(unittest.TestCase):
    def fpu(self, name, regs, **fields):
        s = state()
        for r, bits in regs.items():
            s.fpr[r] = bits
        return bc.expected(name, dict(dict(fs=1, ft=2, fd=3, rt=4, rs=0, imm=0), **fields), s)

    def test_sign_moves_and_ps2_division_by_zero(self):
        self.assertEqual(self.fpu('neg.s', {1: 0x3F800000}).fpr[3], 0xBF800000)
        self.assertEqual(self.fpu('abs.s', {1: 0xFFFFFFFF}).fpr[3], 0x7FFFFFFF)
        self.assertEqual(self.fpu('div.s', {1: 0xBF800000, 2: 0}).fpr[3], 0xFFFFFFFF)  # -max, no infinity
        self.assertEqual(self.fpu('mul.s', {1: 0x3FC00000, 2: 0x40000000}).fpr[3], 0x40400000)

    def test_compares_order_values_beyond_ieee_range(self):
        out = self.fpu('c.lt.s', {1: 0x7F7FFFFF, 2: 0x7FFFFFFF})
        self.assertTrue(out.fcr31 & bc.FCR31_C)  # exponent 255 is a number on the EE, larger than FLT_MAX
        self.assertFalse(self.fpu('c.eq.s', {1: 0x7F800000, 2: 0x7F800001}).fcr31 & bc.FCR31_C)
        self.assertTrue(self.fpu('c.eq.s', {1: 0, 2: 0x80000000}).fcr31 & bc.FCR31_C)

    def test_conversions_truncate_and_saturate(self):
        self.assertEqual(self.fpu('cvt.w.s', {1: 0xC0600000}).fpr[3], (-3) & bc.M32)  # -3.5
        self.assertEqual(self.fpu('cvt.w.s', {1: 0x7F000000}).fpr[3], 0x7FFFFFFF)
        self.assertEqual(self.fpu('mfc1', {1: 0x80000000}, fs=1, rt=4).get(4), 0xFFFFFFFF80000000)

    def vu(self, name, vf, **fields):
        s = state()
        s.vf = [[0, 0, 0, bc.ONE]] + [[0] * 4 for _ in range(31)]
        for r, lanes in vf.items():
            s.vf[r] = list(lanes)
        return bc.expected(name, dict(dict(vs=1, vt=2, vd=3, dest=0xF, rt=0, rs=0, imm=0), **fields), s)

    def test_broadcast_dest_mask_and_vf0(self):
        two, three = 0x40000000, 0x40400000
        out = self.vu('vmuly', {1: [bc.ONE, two, three, bc.ONE], 2: [0, two, 0, 0]}, dest=0b1010)
        self.assertEqual(out.vf[3], [two, 0, 0x40C00000, 0])  # x and z written, y and w kept
        self.assertEqual(self.vu('vadd', {1: [bc.ONE] * 4}, vd=0).vf[0], [0, 0, 0, bc.ONE])
        self.assertEqual(self.vu('vmove', {}, vs=0, vt=5).vf[5], [0, 0, 0, bc.ONE])

    def test_outer_product_rotation_and_ftoi_saturation(self):
        one, two, three = bc.ONE, 0x40000000, 0x40400000
        out = self.vu('vopmula', {1: [one, two, three, 0], 2: [three, one, two, 0]}, dest=0xE)
        self.assertEqual(out.vacc[:3], [0x40800000, 0x41100000, bc.ONE])  # (2*2, 3*3, 1*1)
        self.assertEqual(self.vu('vftoi4', {1: [0x7F000000, 0xFF000000, 0x3F800000, 0]}, vt=4).vf[4],
                         [0x7FFFFFFF, 0x80000000, 16, 0])
        self.assertEqual(self.vu('vmr32', {1: [1, 2, 3, 4]}, vt=4).vf[4], [2, 3, 4, 1])

    def test_clip_judges_xyz_against_w_and_shifts_history(self):
        s = self.vu('vclipw', {1: [0x40000000, 0xC0000000, 0x3F000000, 0], 2: [0, 0, 0, bc.ONE]}, dest=0xE)
        self.assertEqual(s.clip & 0x3F, 0b001001)  # +x beyond, -y beyond, z inside


class Calls(unittest.TestCase):
    def test_link_is_written_even_when_a_likely_link_branch_falls_through(self):
        out = bc.expected('bltzall', dict(rs=1, start=0x100100), state(r1=5))
        self.assertEqual(out.get(bc.LINK_COPY), 0x10010C)
        self.assertEqual((out.get(bc.BRANCH_TAKEN_REG), out.get(bc.BRANCH_SKIPPED_REG)), (0, 1))

    def test_register_call_reads_its_target_before_the_link_and_delay_slot(self):
        s = state(r13=0xAAAA000000200040)
        out = bc.expected('jalr.alias', dict(start=0x100100), s)
        self.assertEqual((out.get(11), out.get(13)), (0x200040, 0x10010C))
        out = bc.expected('jalr.delay-writes-target', dict(start=0x100100), s)
        self.assertEqual((out.get(11), out.get(13), out.get(12)), (0x200040, 0x200044, bc.RETURN_TO))


class Traps(unittest.TestCase):
    def test_traps_that_do_not_fire_change_nothing(self):
        s = state(r1=5, r2=6)
        self.assertEqual(bc.expected('teq', dict(rs=1, rt=2), s).gpr, s.gpr)
        with self.assertRaises(bc.Trap):
            bc.expected('tne', dict(rs=1, rt=2), s)
        with self.assertRaises(bc.Trap):
            bc.expected('tgeiu', dict(rs=1, imm=0xFFFF), state(r1=bc.M64))  # immediate sign-extends, compared unsigned


class Layout(unittest.TestCase):
    def test_every_form_gets_cases_and_the_entry_calls_each(self):
        case_list = bc.cases(random.Random(3))
        names = {c[0] for c in case_list}
        self.assertEqual(names, set(bc.FORMS) | set(bc.BRANCHES) | set(bc.SA_SEQUENCES) | set(bc.FPU_BRANCHES) | set(bc.CALL_LAYOUTS))
        code, starts = bc.program(case_list)
        words = struct.unpack(f'<{len(code) // 4}I', code)
        calls = [((w & 0x3FFFFFF) << 2) for w in words[:2 * len(starts):2]]
        self.assertEqual(calls, starts)
        for (name, case_words, _), start in zip(case_list, starts):
            offset = (start - bc.CODE_BASE) // 4
            self.assertEqual(list(words[offset:offset + len(case_words)]), case_words, name)
            self.assertTrue(bc.JR_RA in case_words or bc.r_type(0x08, bc.SAVE_RA) in case_words, name)

    def test_cases_never_use_the_return_or_counter_registers(self):
        for name, _, fields in bc.cases(random.Random(5)):
            for key in ('rs', 'rt', 'rd'):
                self.assertNotIn(fields.get(key, 0), (bc.RA, bc.BRANCH_TAKEN_REG, bc.BRANCH_SKIPPED_REG), name)

    def test_generated_memory_accesses_stay_in_the_window(self):
        rng = random.Random(9)
        for name, _, fields in bc.cases(rng):
            if bc.FORMS.get(name, {}).get('layout') != 'mem':
                continue
            for _ in range(50):
                s = bc.random_state(rng, name, fields)
                try:
                    bc.expected(name, fields, s)  # out-of-window bytes would raise IndexError
                except bc.Trap:
                    pass

    def test_state_round_trips_through_the_harness_format(self):
        s = bc.random_state(random.Random(2), 'addu', dict(rs=1, rt=2, rd=3))
        back = bc.unpack(bc.pack(s))
        self.assertEqual((back.gpr, back.hi, back.lo, back.hi1, back.lo1, back.mem), (s.gpr, s.hi, s.lo, s.hi1, s.lo1, s.mem))


if __name__ == '__main__':
    unittest.main()
