"""Compiled VU0 macro helpers (and FPU MAX.S/MIN.S) against the behaviour-check model, and the patch
that routes the recompiler to them; no game or recompiler build needed."""
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

import behaviour_check as bc

ROOT = Path(__file__).resolve().parents[1]
TABLE = (ROOT / 'tools/recomp/vu0_corrections.h').read_text()
SPECIAL1 = {int(code, 16): helper for code, helper in
            re.findall(r'\{0x([0-9A-F]+), "(\w+)"\}', TABLE.split('kSpecial2')[0])}
SPECIAL2 = {int(code, 16): helper for code, helper in
            re.findall(r'\{0x([0-9A-F]+), "(\w+)"\}', TABLE.split('kSpecial2')[1])}
HELPERS = sorted(set(SPECIAL1.values()) | set(SPECIAL2.values()))


def helper_for(word):
    """Python reading of vu0_corrections::helperFor."""
    if word >> 26 != 0x12 or not word & (1 << 25):
        return None
    function = word & 0x3F
    if function >= 0x3C:
        return SPECIAL2.get((((word >> 6) & 0x1F) << 2) | (word & 3))
    return SPECIAL1.get(function)


PROBE = r'''
#include <cstdint>
#include <cstdio>
struct R5900Context {
    alignas(16) uint32_t vu0_vf[32][4]; alignas(16) uint32_t vu0_acc[4];
    float vu0_q, vu0_i; uint32_t vu0_clip_flags, vu0_mac_flags; uint16_t vu0_status; uint16_t vi[16];
    float f[32]; float f_acc; uint32_t fcr31;
};
#include "ps2_scalar_fpu.h"
#include "ps2_vu0_ops.h"
using Fn = void (*)(R5900Context *, uint32_t);
static const Fn kHelpers[] = {@HELPERS@};
int main() {
    uint32_t head[2], io[153];
    R5900Context c{};
    while (std::fread(head, 4, 2, stdin) == 2 && std::fread(io, 4, 153, stdin) == 153) {
        std::memcpy(c.vu0_vf, io, 512); std::memcpy(c.vu0_acc, io + 128, 16);
        std::memcpy(&c.vu0_q, io + 132, 4); std::memcpy(&c.vu0_i, io + 133, 4);
        c.vu0_clip_flags = io[134]; c.vu0_mac_flags = io[135]; c.vu0_status = (uint16_t)io[136];
        for (int k = 0; k < 16; ++k) c.vi[k] = (uint16_t)io[137 + k];
        if (head[0] == 0xFFFFFFFFu) {  // FPU MAX.S then MIN.S of vf1.x and vf1.y into vf2.x / vf2.y
            float a, b; std::memcpy(&a, &io[4], 4); std::memcpy(&b, &io[5], 4);
            float mx = ps2_vu0::fmax(a, b), mn = ps2_vu0::fmin(a, b);
            std::memcpy(&c.vu0_vf[2][0], &mx, 4); std::memcpy(&c.vu0_vf[2][1], &mn, 4);
        } else {
            kHelpers[head[0]](&c, head[1]);
        }
        std::memcpy(io, c.vu0_vf, 512); std::memcpy(io + 128, c.vu0_acc, 16);
        std::memcpy(io + 132, &c.vu0_q, 4); std::memcpy(io + 133, &c.vu0_i, 4);
        io[134] = c.vu0_clip_flags; io[135] = c.vu0_mac_flags; io[136] = c.vu0_status;
        for (int k = 0; k < 16; ++k) io[137 + k] = c.vi[k];
        std::fwrite(io, 4, 153, stdout);
    }
    return 0;
}
'''


def _compiler():
    return shutil.which('clang++') or shutil.which('g++')


@unittest.skipUnless(_compiler() and (shutil.which('cc') or shutil.which('clang')), 'C/C++ compilers required')
class ReferenceHelpers(unittest.TestCase):
    def _run(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'probe.cpp', Path(tmp) / 'probe'
            source.write_text(PROBE.replace('@HELPERS@', ', '.join(f'ps2_vu0::{h}' for h in HELPERS)))
            built = subprocess.run([_compiler(), '-std=c++20', '-O2', '-include', 'cstring',
                                    '-I' + str(ROOT / 'runtime/mmi'), '-I' + str(ROOT / 'tools'), str(source), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            payload = b''.join(struct.pack('<2I', index, word) + struct.pack('<153I', *state.vu_words())
                               for index, word, state in records)
            run = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            return [list(struct.unpack_from('<153I', run.stdout, 612 * k)) for k in range(len(records))]

    def test_every_routed_form_matches_the_independent_model(self):
        rng = random.Random(11)
        records, expected = [], []
        for name, words, fields in bc.cases(rng):
            if name not in bc.VU_FORMS or not helper_for(words[0]):
                continue
            for _ in range(60):
                state = bc.random_state(rng, name, fields)
                try:
                    want = bc.expected(name, fields, state)
                except bc.Trap:
                    continue
                records.append((HELPERS.index(helper_for(words[0])), words[0], state))
                expected.append((name, fields, state, want))
        self.assertGreater(len({n for n, _, _, _ in expected}), 100)
        for (name, fields, before, want), words in zip(expected, self._run(records)):
            got = want.copy()
            got.vf = [words[4 * r:4 * r + 4] for r in range(32)]
            got.vacc, (got.q, got.i, got.clip, got.mac, got.status), got.vi = words[128:132], words[132:137], words[137:153]
            diffs = bc.describe(want, got)
            if diffs:
                self.assertTrue(bc.rounding_difference(name, fields, before, want, got), f'{name} {fields} {diffs}')

    def test_fpu_max_min_order_exponent_255_as_numbers(self):
        rng = random.Random(5)
        records, pairs = [], []
        for _ in range(400):
            state = bc.random_state(rng, 'vadd', dict(vs=1, vt=2, vd=3, dest=15))
            a, b = state.vf[1][0], state.vf[1][1]
            if bc._denormal(a) or bc._denormal(b) or (a & 0x7FFFFFFF == 0 and b & 0x7FFFFFFF == 0):
                continue
            records.append((0xFFFFFFFF, 0, state))
            pairs.append((a, b))
        for (a, b), words in zip(pairs, self._run(records)):
            order = bc.ps2float().ps2f_compare(a, b)
            if order == 0 and a != b:
                continue
            self.assertEqual(words[8], a if order >= 0 else b, (hex(a), hex(b)))
            self.assertEqual(words[9], a if order <= 0 else b, (hex(a), hex(b)))

    def test_table_names_only_defined_helpers(self):
        ops = (ROOT / 'runtime/mmi/ps2_vu0_ops.h').read_text()
        for helper in HELPERS:
            self.assertRegex(ops, rf'(inline void {helper}\(R5900Context \*c|PS2_VU0_ARITH\({helper},)', helper)


class ReferenceFormsPatch(unittest.TestCase):
    def _added(self, patch, path):
        added = re.search(r'\+\+\+ b/' + re.escape(path) + r'\n@@[^\n]*\n((?:\+[^\n]*\n)+)', patch)
        self.assertIsNotNone(added, path)
        return ''.join(line[1:] + '\n' for line in added.group(1).splitlines())

    def test_patch_carries_the_reviewed_headers_and_routes_the_translators(self):
        patch = (ROOT / 'patches/vu0-reference-forms.patch').read_text()
        self.assertEqual(self._added(patch, 'ps2xRecomp/include/ps2recomp/vu0_corrections.h'), TABLE)
        self.assertEqual(self._added(patch, 'ps2xRuntime/include/ps2_vu0_ops.h'),
                         (ROOT / 'runtime/mmi/ps2_vu0_ops.h').read_text())
        vu = patch.split('+++ b/ps2xRecomp/src/lib/vu_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertIn('+            if (const char *helper = vu0_corrections::helperFor(inst.raw))', vu)
        fpu = patch.split('+++ b/ps2xRecomp/src/lib/fpu_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertIn('ps2_vu0::fmax', fpu)
        vu_all = patch.split('+++ b/ps2xRecomp/src/lib/vu_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertIn('+                return "// QMTC2 to VF0 ignored: VF0 is read-only";', vu_all)
        load = patch.split('+++ b/ps2xRecomp/src/lib/instruction_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertIn('+                return "// LQC2 to VF0 ignored: VF0 is read-only";', load)
        self.assertIn('ps2_vu0::fmin', fpu)
        self.assertIn('+#include "ps2_vu0_ops.h"', patch.split('+++ b/ps2xRuntime/include/ps2_runtime_macros.h', 1)[1])
        import native
        self.assertIn('patches/vu0-reference-forms.patch', native.RUNTIME_PATCHES)


if __name__ == '__main__':
    unittest.main()
