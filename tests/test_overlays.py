"""Overlay translation planning on synthetic programs; no game code or translator run."""
from pathlib import Path
import hashlib
import json
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overlays
import pointer_entries
from feedback import executable_spans

JR_RA, NOP = 0x03E00008, 0


def functions(base, count, words_each=16):
    """`count` functions at `base`, each calling the next: prologue ... jr ra, restore sp."""
    out = []
    for i in range(count):
        callee = base + ((i + 1) % count) * words_each * 4
        body = [0x27BDFFF0, 0xFFBF0000, 0x0C000000 | (callee >> 2) & 0x03FFFFFF, NOP]
        body += [0x24020000 | i & 0x7FFF] * (words_each - len(body) - 4)
        body += [0xDFBF0000, JR_RA, 0x27BD0010, NOP]
        out += body
    return struct.pack(f'<{len(out)}I', *out)


def noise(size, seed=b'overlay'):
    data, block = bytearray(), seed
    while len(data) < size:
        block = hashlib.sha256(block).digest()
        data += block
    return bytes(data[:size])


class CodeSpans(unittest.TestCase):
    def test_code_between_data_is_found_and_edges_are_exact(self):
        code = functions(0x200000, 512)  # 32 KiB
        data = noise(8192 + 12) + code + noise(8192, b'tail')
        spans = overlays.code_spans(data)
        self.assertEqual(spans, [(8192 + 12, 8192 + 12 + len(code) - 4)])  # ends after the delay slot

    def test_big_called_function_outside_dense_blocks_is_added(self):
        base = 0x300000
        dense = functions(base, 256)                       # 16 KiB of dense code
        big_at = len(dense) + 4096                         # data, then a 12 KiB leaf function
        big = [0x27BDFF00] + [0x24020001] * 3070 + [JR_RA, 0x27BD0100]
        caller = struct.unpack_from('<I', dense, 8)[0]
        raw = bytearray(dense + noise(4096) + struct.pack(f'<{len(big)}I', *big) + noise(4096, b'end'))
        struct.pack_into('<I', raw, 8, 0x0C000000 | ((base + big_at) >> 2))  # first function now calls it
        spans = overlays.extend_spans(bytes(raw), base, overlays.code_spans(bytes(raw)))
        self.assertIn((big_at, big_at + 4 * len(big)), spans)
        self.assertNotEqual(caller, 0)

    def test_data_pointer_to_non_prologue_is_not_code(self):
        base = 0x300000
        dense = functions(base, 256)
        raw = dense + struct.pack('<I', base + len(dense) + 64) + noise(4096)
        spans = overlays.extend_spans(raw, base, overlays.code_spans(raw))
        self.assertEqual(spans, overlays.code_spans(raw))


class WrappedExecutable(unittest.TestCase):
    def test_only_code_spans_are_executable_and_scan_sees_section_starts(self):
        base = 0x200000
        code = functions(base, 64)
        data = noise(4096) + code + noise(4096, b'x')
        spans = [(4096, 4096 + len(code))]
        raw = overlays.wrap_elf(data, base, spans)
        self.assertEqual([(a, b) for a, b, _ in executable_spans(raw)], [(base + 4096, base + 4096 + len(code))])
        self.assertEqual(struct.unpack_from('<I', raw, 24)[0], base + 4096)  # entry at the first code byte
        found = dict(pointer_entries.scan(raw))
        self.assertEqual(found.get(base + 4096), 'section_start')


class Candidates(unittest.TestCase):
    def region(self, offset, size, base, votes=300, sampled=800):
        return dict(source='image', file='unlisted_sectors', kind='ee_code_candidate', offset=offset, bytes=size,
                    estimated_load_address=f'0x{base:08x}', base_votes=votes, sampled_call_targets=sampled)

    def test_regions_of_one_program_are_joined_and_weak_bases_rejected(self):
        census = dict(regions=[self.region(0x100000, 0x10000, 0x200000),
                               self.region(0x140000, 0x20000, 0x240000),   # same program: base moves with offset
                               self.region(0x800000, 0x10000, 0x200000),   # another program, same address
                               self.region(0x900000, 0x10000, 0x200000, votes=10),
                               dict(kind='iop_module', offset=0, bytes=1)])
        accepted, rejected = overlays.candidates(census)
        self.assertEqual([(oid, r['offset'], r['bytes']) for oid, r in accepted],
                         [('o00', 0x100000, 0x60000), ('o01', 0x800000, 0x10000)])
        self.assertEqual(accepted[0][1]['joined_regions'], 2)
        self.assertEqual([r['offset'] for r in rejected], [0x900000])


class Signatures(unittest.TestCase):
    def test_words_differ_from_other_programs_and_the_boot_image(self):
        base = 0x200000
        a = bytearray(functions(base, 64)); b = bytearray(a)
        struct.pack_into('<I', b, 400, 0x11111111); struct.pack_into('<I', b, len(b) - 400, 0x22222222)
        struct.pack_into('<I', a, 800, 0x33333333)
        boot = (base, bytes(a[:804]))  # the boot image holds a's first words, including 0x33333333
        sigs = overlays.signatures({'a': (base, bytes(a), [(0, len(a))]), 'b': (base, bytes(b), [(0, len(b))])}, boot, count=2)
        self.assertEqual(sigs['b'], [(base + 400, 0x11111111), (base + len(b) - 400, 0x22222222)])
        # a matches b everywhere except where b was changed, and the boot image holds a's front
        # (including 0x33333333): only the word near the end distinguishes a.
        self.assertEqual(sigs['a'], [(base + len(a) - 400, struct.unpack_from('<I', a, len(a) - 400)[0])])
        for address, value in sigs['a'] + sigs['b']:
            at = address - base
            if at < len(boot[1]):
                self.assertNotEqual(struct.unpack_from('<I', boot[1], at)[0], value)


class Qualify(unittest.TestCase):
    def test_every_generated_symbol_and_header_is_overlay_qualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp)
            (g / 'ps2_recompiled_functions.h').write_text(
                'void sub_00200000_0x200000(uint8_t* rdram, R5900Context* ctx, PS2Runtime *runtime);\n'
                'void sub_00200040_0x200040(uint8_t* rdram, R5900Context* ctx, PS2Runtime *runtime);\n')
            (g / 'ps2_recompiled_stubs.h').write_text('// stubs\n')
            (g / 'sub_00200000_0x200000.cpp').write_text(
                '#include <ps2_recompiled_functions.h>\n#include <ps2_recompiled_stubs.h>\n'
                'void sub_00200000_0x200000(uint8_t* rdram, R5900Context* ctx, PS2Runtime *runtime) { sub_00200040_0x200040(rdram, ctx, runtime); }\n')
            (g / 'register_functions.cpp').write_text(
                '#include "ps2_runtime.h"\n#include <ps2_recompiled_functions.h>\n'
                'extern const uint32_t g_ps2RecompiledFunctionTableBase = 0x200000u;\n'
                'PS2Runtime::RecompiledFunction g_ps2RecompiledFunctionTable[17u] = {};\n'
                'g_ps2RecompiledFunctionTable[0] = sub_00200000_0x200000;\n')
            self.assertEqual(overlays.qualify(g, 'o03'), 2)
            names = sorted(p.name for p in g.iterdir())
            self.assertEqual(names, ['o03_recompiled_functions.h', 'o03_recompiled_stubs.h', 'o03_register_functions.cpp',
                                     'o03_sub_00200000_0x200000.cpp'])
            body = (g / 'o03_sub_00200000_0x200000.cpp').read_text()
            self.assertIn('void o03_sub_00200000_0x200000(', body)
            self.assertIn('o03_sub_00200040_0x200040(rdram', body)
            self.assertIn('<o03_recompiled_stubs.h>', body)
            table = (g / 'o03_register_functions.cpp').read_text()
            self.assertNotIn('g_ps2RecompiledFunctionTable', table)
            self.assertIn('g_ps2Overlay_o03_FunctionTable[0] = o03_sub_00200000_0x200000;', table)
            self.assertIn('extern const uint32_t g_ps2Overlay_o03_FunctionTableBase = 0x200000u;', table)

    def test_manifest_registers_tables_with_their_signatures(self):
        text = overlays.manifest_source([dict(id='o00', signature=[(0x200000, 0x27BDFFF0), (0x200100, 1)])])
        self.assertIn('{0x200000u, 0x27bdfff0u}, {0x200100u, 0x00000001u}', text)
        self.assertIn('ps2port::overlayRegistry().add({"o00", g_ps2Overlay_o00_FunctionTableBase,', text)
        self.assertIn('o00_signature, 2u});', text)


class BuildOptIn(unittest.TestCase):
    def test_overlays_join_the_native_build_only_when_the_profile_asks(self):
        import native
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            self.assertEqual(native.overlay_build_directory({}, private), '')
            with self.assertRaisesRegex(ValueError, 'no overlays were registered'):
                native.overlay_build_directory({'overlay_build': True}, private)
            (private / 'analysis').mkdir()
            record = private / 'analysis/overlay-translation.json'
            record.write_text(json.dumps(dict(accepted_overlays=2)))
            with self.assertRaisesRegex(ValueError, 'syntax sample'):
                native.overlay_build_directory({'overlay_build': True}, private)
            record.write_text(json.dumps(dict(accepted_overlays=2, syntax_sample=dict(success=True))))
            self.assertEqual(native.overlay_build_directory({'overlay_build': True}, private), str(private / 'overlays'))


if __name__ == '__main__':
    unittest.main()
