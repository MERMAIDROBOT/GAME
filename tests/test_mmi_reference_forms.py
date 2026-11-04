"""Compiled MMI reference helpers against the behaviour-check model, and the patch that routes the
recompiler to them; no game or recompiler build needed."""
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
TABLE = (ROOT / 'tools/recomp/mmi_corrections.h').read_text()
HELPERS = re.findall(r'\{0x([0-9A-Fa-f]+), (-?(?:0x)?[0-9A-Fa-f]+), "(\w+)"\}', TABLE)
# behaviour_check form names differ only where PMFHL/PMTHL carry their variant
FORM = {helper: helper.replace('_', '.') for _, _, helper in HELPERS}

PROBE = r'''
#include <cstdint>
#include <cstdio>
struct R5900Context { alignas(16) unsigned char r[32][16]; uint64_t hi, lo, hi1, lo1; };
#include "ps2_mmi_ops.h"
using Fn = void (*)(R5900Context *, int, int, int, int);
static const Fn kHelpers[] = {@HELPERS@};
int main() {
    int32_t head[5];
    R5900Context c;
    while (std::fread(head, 4, 5, stdin) == 5) {
        if (std::fread(c.r, 16, 32, stdin) != 32 || std::fread(&c.hi, 8, 4, stdin) != 4) return 2;
        kHelpers[head[0]](&c, head[1], head[2], head[3], head[4]);
        std::fwrite(c.r, 16, 32, stdout);
        std::fwrite(&c.hi, 8, 4, stdout);
    }
    return 0;
}
'''


def _pack(index, fields, state):
    head = struct.pack('<5i', index, fields['rd'], fields['rs'], fields['rt'], fields['sa'])
    regs = b''.join(struct.pack('<QQ', *state.gpr[r]) for r in range(32))
    return head + regs + struct.pack('<4Q', state.hi, state.lo, state.hi1, state.lo1)


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'), 'C++ compiler required')
class ReferenceHelpers(unittest.TestCase):
    def test_every_helper_matches_the_independent_model(self):
        names = [helper for _, _, helper in HELPERS]
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'probe.cpp', Path(tmp) / 'probe'
            source.write_text(PROBE.replace('@HELPERS@', ', '.join(f'ps2_mmi::{n}' for n in names)))
            built = subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++17', '-O2', '-Wall', '-Werror',
                                    '-I' + str(ROOT / 'runtime/mmi'), str(source), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            rng = random.Random(7)
            inputs, expected = bytearray(), []
            for index, name in enumerate(names):
                for _ in range(200):
                    rs, rt, rd = rng.sample(bc.USABLE, 3)
                    if rng.random() < 0.2:
                        rd = rt = rs
                    if rng.random() < 0.1:
                        rd = 0
                    fields = dict(rs=rs, rt=rt, rd=rd, sa=rng.randrange(32), imm=0)
                    state = bc.random_state(rng, FORM[name], fields)
                    if FORM[name] in bc.MMI_FORMS and rng.random() < 0.2:
                        state.gpr[rt] = list(state.gpr[rs])
                    inputs += _pack(index, fields, state)
                    expected.append((name, fields, bc.expected(FORM[name], fields, state)))
            run = subprocess.run([str(binary)], input=bytes(inputs), capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            size = 32 * 16 + 32
            self.assertEqual(len(run.stdout), size * len(expected))
            for k, (name, fields, want) in enumerate(expected):
                raw = run.stdout[k * size:(k + 1) * size]
                gpr = [list(struct.unpack_from('<QQ', raw, 16 * r)) for r in range(32)]
                got = bc.State(gpr, *struct.unpack_from('<4Q', raw, 512), b'')
                got.mem, got.fpr, got.acc, got.fcr31, got.vumem = want.mem, want.fpr, want.acc, want.fcr31, want.vumem
                got.vf, got.vacc, got.q, got.i, got.clip, got.mac, got.status, got.vi = want.vf, want.vacc, want.q, want.i, want.clip, want.mac, want.status, want.vi
                self.assertEqual(bc.describe(want, got), [], f'{name} {fields}')

    def test_table_names_only_defined_helpers_and_known_forms(self):
        ops = (ROOT / 'runtime/mmi/ps2_mmi_ops.h').read_text()
        for _, _, helper in HELPERS:
            self.assertRegex(ops, rf'inline void {helper}\(R5900Context \*c', helper)
            self.assertIn(FORM[helper], bc.MMI_FORMS, helper)
        self.assertEqual(len({(f, s) for f, s, _ in HELPERS}), len(HELPERS))


class ReferenceFormsPatch(unittest.TestCase):
    def _added(self, patch, path):
        added = re.search(r'\+\+\+ b/' + re.escape(path) + r'\n@@[^\n]*\n((?:\+[^\n]*\n)+)', patch)
        self.assertIsNotNone(added, path)
        return ''.join(line[1:] + '\n' for line in added.group(1).splitlines())

    def test_patch_carries_the_reviewed_headers_and_routes_the_translator(self):
        patch = (ROOT / 'patches/mmi-reference-forms.patch').read_text()
        self.assertEqual(self._added(patch, 'ps2xRecomp/include/ps2recomp/mmi_corrections.h'), TABLE)
        self.assertEqual(self._added(patch, 'ps2xRuntime/include/ps2_mmi_ops.h'),
                         (ROOT / 'runtime/mmi/ps2_mmi_ops.h').read_text())
        translator = patch.split('+++ b/ps2xRecomp/src/lib/mmi_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertIn('+        if (const char *helper = mmi_corrections::helperFor(function, sa))', translator)
        macros = patch.split('+++ b/ps2xRuntime/include/ps2_runtime_macros.h', 1)[1]
        self.assertIn('+#include "ps2_mmi_ops.h"', macros)
        # MOVZ/MOVN are 64-bit moves: the upper half of rd is kept
        special = patch.split('+++ b/ps2xRecomp/src/lib/special_translator.cpp', 1)[1].split('diff --git', 1)[0]
        self.assertEqual(special.count('SET_GPR_U64(ctx, {}, GPR_U64(ctx, {}))'), 2)
        self.assertNotIn('+            return fmt::format("if (GPR_U64(ctx, {}) == 0) SET_GPR_VEC', special)
        import native
        self.assertIn('patches/mmi-reference-forms.patch', native.RUNTIME_PATCHES)


if __name__ == '__main__':
    unittest.main()
