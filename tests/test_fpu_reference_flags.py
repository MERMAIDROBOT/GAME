"""The patch that routes EE FPU arithmetic through flag-setting helpers, and the knowledge rule that
accepts the resulting DIV.S/RSQRT.S emission; no game needed."""
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

import behaviour_check as bc
import fpu

ROOT = Path(__file__).resolve().parents[1]


PROBE = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
struct R5900Context {
    alignas(16) uint32_t vu0_vf[32][4]; alignas(16) uint32_t vu0_acc[4];
    float vu0_q, vu0_i; uint32_t vu0_clip_flags, vu0_mac_flags; uint16_t vu0_status; uint16_t vi[16];
    float f[32]; float f_acc; uint32_t fcr31;
};
#include "ps2_scalar_fpu.h"
#include "ps2_vu0_ops.h"
int main() {
    uint32_t io[35];
    R5900Context c{};
    while (std::fread(io, 4, 35, stdin) == 35) {
        std::memcpy(c.f, io, 128); std::memcpy(&c.f_acc, io + 32, 4); c.fcr31 = io[33];
        const float a = c.f[1], b = c.f[2];
        float *fd = &c.f[3];
        switch (io[34]) {
        case 0: *fd = ps2_fpu::add(&c, a, b); break;
        case 1: *fd = ps2_fpu::sub(&c, a, b); break;
        case 2: *fd = ps2_fpu::mul(&c, a, b); break;
        case 3: *fd = ps2_fpu::div(&c, a, b); break;
        case 4: *fd = ps2_fpu::sqrt(&c, b); break;
        case 5: *fd = ps2_fpu::rsqrt(&c, a, b); break;
        case 6: *fd = ps2_fpu::madd(&c, c.f_acc, a, b); break;
        case 7: *fd = ps2_fpu::msub(&c, c.f_acc, a, b); break;
        case 8: c.f_acc = ps2_fpu::madd(&c, c.f_acc, a, b); break;
        case 9: *fd = ps2_fpu::abs(&c, a); break;
        case 10: *fd = ps2_fpu::max(&c, a, b); break;
        }
        std::memcpy(io, c.f, 128); std::memcpy(io + 32, &c.f_acc, 4); io[33] = c.fcr31;
        std::fwrite(io, 4, 35, stdout);
    }
    return 0;
}
'''
FORMS = ['add.s', 'sub.s', 'mul.s', 'div.s', 'sqrt.s', 'rsqrt.s', 'madd.s', 'msub.s', 'madda.s', 'abs.s', 'max.s']
EDGES = [0x7F7FFFFF, 0x7FFFFFFF, 0xFF7FFFFF, 0x00800000, 0x00800001, 0x80800000, 0x00FFFFFF, 0, 0x80000000,
         0x00000001, 0x3F000000, 0x7F000000]


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'), 'C++ compiler required')
class FpuHelpers(unittest.TestCase):
    def test_values_and_fcr31_flags_match_the_model(self):
        rng = random.Random(4)
        fields = dict(fs=1, ft=2, fd=3, rt=0, rs=0, imm=0)
        records, expected = [], []
        for index, name in enumerate(FORMS):
            for _ in range(1500):
                state = bc.random_state(rng, name, fields)
                if rng.random() < 0.4:  # overflow, underflow, zero divisors and huge operands
                    state.fpr[1], state.fpr[2], state.acc = rng.choice(EDGES), rng.choice(EDGES), rng.choice(EDGES)
                try:
                    want = bc.expected(name, fields, state)
                except bc.Trap:
                    continue
                records.append(struct.pack('<35I', *state.fpr, state.acc, state.fcr31, index))
                expected.append((name, state, want))
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'probe.cpp', Path(tmp) / 'probe'
            source.write_text(PROBE)
            built = subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++20', '-O2',
                                    '-I' + str(ROOT / 'runtime/mmi'), '-I' + str(ROOT / 'tools'), str(source), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            out = subprocess.run([str(binary)], input=b''.join(records), capture_output=True, timeout=60).stdout
        flags_seen = 0
        for k, (name, before, want) in enumerate(expected):
            words = struct.unpack_from('<35I', out, 140 * k)
            got = want.copy()
            got.fpr, got.acc, got.fcr31 = list(words[:32]), words[32], words[33]
            flags_seen |= want.fcr31 & ~before.fcr31
            if bc.describe(want, got):
                self.assertTrue(bc.rounding_difference(name, fields, before, want, got), f'{name} {bc.describe(want, got)}')
        for flag in (bc.FCR_O, bc.FCR_U, bc.FCR_D, bc.FCR_I, bc.FCR_SO, bc.FCR_SU, bc.FCR_SD, bc.FCR_SI):
            self.assertTrue(flags_seen & flag, hex(flag))  # every flag was raised somewhere


class FpuFlagsPatch(unittest.TestCase):
    def test_every_arithmetic_form_calls_a_flag_setting_helper(self):
        patch = (ROOT / 'patches/fpu-reference-flags.patch').read_text()
        added = '\n'.join(line for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        removed = '\n'.join(line for line in patch.splitlines() if line.startswith('-') and not line.startswith('---'))
        for helper in ('add', 'sub', 'mul', 'div', 'sqrt', 'rsqrt', 'abs', 'neg', 'madd', 'msub', 'max', 'min'):
            self.assertRegex(added, rf'ps2_fpu::{helper}\(ctx,', helper)
        self.assertIn('0x100000', removed)  # the old divide-by-zero update set an unrelated bit
        self.assertIn('1.0f / sqrtf(ctx->f[{}])', removed)  # RSQRT dropped its numerator's operand order
        ops = (ROOT / 'runtime/mmi/ps2_vu0_ops.h').read_text()
        for helper in re.findall(r'ps2_fpu::(\w+)\(ctx,', added):
            self.assertRegex(ops, rf'inline float {helper}\(R5900Context \*c', helper)
        import native
        names = native.RUNTIME_PATCHES
        self.assertLess(names.index('patches/vu0-reference-forms.patch'), names.index('patches/fpu-reference-flags.patch'))

    def test_knowledge_accepts_the_patched_division_and_reciprocal_root(self):
        for funct, call in ((0x03, 'div'), (0x16, 'rsqrt')):
            word = 0x46000000 | (2 << 16) | (1 << 11) | (3 << 6) | funct  # fd=3, fs=1, ft=2
            self.assertEqual(fpu.reference_call(word), f'ctx->f[3] = ps2_fpu::{call}(ctx, ctx->f[1], ctx->f[2]);')
            source = f'// 0x1000: 0x{word:08x} x\nctx->pc = 0x1000u;\n{fpu.reference_call(word)}\n'
            self.assertEqual(fpu.rewrite(source), (source, 1, 0))

if __name__ == '__main__':
    unittest.main()
