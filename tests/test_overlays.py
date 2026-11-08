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
    def test_direct_branch_retains_alternate_return_pair_without_executing_padding(self):
        words=[0x27bdfff0,0x10800004,0,0,JR_RA,0x27bd0010,JR_RA,0x2402ffff]
        raw=struct.pack('<8I',*words)+noise(128)
        self.assertEqual(overlays.extend_branch_tails(raw,0x200000,[(0,24)]),[(0,32)])
        # An address in unrelated data, with no accepted incoming branch, stays data.
        words[1]=0
        self.assertEqual(overlays.extend_branch_tails(struct.pack('<8I',*words)+noise(128),0x200000,[(0,24)]),[(0,24)])

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
    def test_accepted_overlays_join_automatically_with_explicit_boot_only_opt_out(self):
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
            self.assertEqual(native.overlay_build_directory({}, private), str(private / 'overlays'))
            self.assertEqual(native.overlay_build_directory({'overlay_build': False}, private), '')
            with self.assertRaisesRegex(ValueError, 'boolean'):
                native.overlay_build_directory({'overlay_build': 'yes'}, private)
            (private/'overlays').mkdir();(private/'overlays/overlay_manifest.cpp').write_text('// accepted tables')
            with self.assertRaisesRegex(ValueError, 'instruction knowledge'):
                native.overlay_build_receipt({},private)
            from knowledge import load,sha
            info=json.loads(record.read_text());info.update(knowledge_bank_sha256=sha(json.dumps(load(),sort_keys=True).encode()),qualified_metadata_version=2);record.write_text(json.dumps(info))
            before=native.overlay_build_receipt({},private)
            (private/'overlays/overlay_manifest.cpp').write_text('// changed tables')
            self.assertNotEqual(before,native.overlay_build_receipt({},private))


class UnityMetadata(unittest.TestCase):
    def test_distinct_overlay_guards_and_initializers_compile_together(self):
        import shutil,subprocess
        if not shutil.which('clang++'):self.skipTest('clang++ required')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'ps2_runtime.h').write_text('#pragma once\n#include <cstdint>\nstruct R5900Context;class PS2Runtime {public:using RecompiledFunction=void(*)(uint8_t*,R5900Context*,PS2Runtime*);};\n')
            (root/'ps2_stubs.h').write_text('');(root/'ps2_syscalls.h').write_text('')
            for oid in ('o00','o01'):
                g=root/oid;g.mkdir()
                (g/'ps2_recompiled_functions.h').write_text('#ifndef PS2_RECOMPILED_FUNCTIONS_H\n#define PS2_RECOMPILED_FUNCTIONS_H\n#include <cstdint>\nstruct R5900Context;class PS2Runtime;\nvoid sub_00200000_0x200000(uint8_t* rdram,R5900Context* ctx,PS2Runtime* runtime);\n#endif\n')
                (g/'ps2_recompiled_stubs.h').write_text('#ifndef PS2_RECOMPILED_STUBS_H\n#define PS2_RECOMPILED_STUBS_H\n#endif\n')
                (g/'register_functions.cpp').write_text('#include "ps2_runtime.h"\n#include <ps2_recompiled_functions.h>\nextern const uint32_t g_ps2RecompiledFunctionTableBase=0x200000,g_ps2RecompiledFunctionTableEnd=0x200004,g_ps2RecompiledFunctionTableSlotCount=1;\nPS2Runtime::RecompiledFunction g_ps2RecompiledFunctionTable[1]={};\nnamespace {struct GeneratedFunctionTableInitializer {GeneratedFunctionTableInitializer(){g_ps2RecompiledFunctionTable[0]=sub_00200000_0x200000;}};static const GeneratedFunctionTableInitializer g_generatedFunctionTableInitializer;}\n')
                overlays.qualify(g,oid)
            unit=root/'unity.cpp';unit.write_text('#include "o00/o00_register_functions.cpp"\n#include "o01/o01_register_functions.cpp"\n')
            command=['clang++','-std=c++17','-fsyntax-only','-I'+str(root),'-I'+str(root/'o00'),'-I'+str(root/'o01'),str(unit)]
            good=subprocess.run(command,capture_output=True,text=True);self.assertEqual(good.returncode,0,good.stderr)
            # Reintroduce the shared guards and initializer names: the fixture must detect the old bug.
            for oid in ('o00','o01'):
                for path in (root/oid).iterdir():
                    path.write_text(path.read_text().replace(oid+'_PS2_RECOMPILED','PS2_RECOMPILED').replace(oid+'_GeneratedFunctionTableInitializer','GeneratedFunctionTableInitializer').replace(oid+'_g_generatedFunctionTableInitializer','g_generatedFunctionTableInitializer'))
            bad=subprocess.run(command,capture_output=True,text=True);self.assertNotEqual(bad.returncode,0)

class ConcurrentVuCompatibility(unittest.TestCase):
    def test_native_guard_rejects_old_resident_code_and_records_exact_gap(self):
        import native
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'generated').mkdir();(root/'analysis').mkdir()
            source=root/'generated/body.cpp'
            source.write_text('// 0x1000: 0x48210800 qmfc2.ni\nSET_GPR_VEC(ctx, 1, old);\n')
            with self.assertRaisesRegex(ValueError, 'Resident translation predates'):
                native.require_vu0_translation_compatibility(root)
            report=json.loads((root/'analysis/vu0-translation-compatibility.json').read_text())
            self.assertEqual(report['missing'][0]['address'],'1000')
            source.write_text('// 0x1000: 0x48210800 qmfc2.ni\nPS2_VU0_SYNC_READ(ctx, runtime); SET_GPR_VEC(ctx, 1, old);\n')
            self.assertEqual(native.require_vu0_translation_compatibility(root),dict(checked=1,passed=True))

    def test_opcode_audit_distinguishes_reads_writes_interlocks_and_macros(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'body.cpp'
            instructions = [
                (0x48210800, 'PS2_VU0_SYNC_READ(ctx, runtime);'),
                (0x48210801, 'PS2_VU0_INTERLOCK(ctx, runtime);'),
                (0x48a19800, 'PS2_VU0_SYNC_WRITE(ctx, runtime);'),
                (0x48a19801, 'PS2_VU0_INTERLOCK(ctx, runtime); PS2_VU0_SYNC_WRITE(ctx, runtime);'),
                (0x4a9e0784, 'PS2_VU0_INTERLOCK(ctx, runtime);'),
                (0x46157bc2, 'unrelated();'),  # COP1 is not a VU transfer
            ]
            text = ''.join(f'// 0x{0x1000+i*4:x}: 0x{word:08x} instruction\n{body}\n' for i, (word, body) in enumerate(instructions))
            path.write_text(text)
            result = overlays.vu0_sync_audit(root)
            self.assertTrue(result['passed']); self.assertEqual(result['checked'], 5)
            path.write_text(text.replace('PS2_VU0_SYNC_WRITE(ctx, runtime);', ''))
            missing = overlays.vu0_sync_audit(root)
            self.assertFalse(missing['passed']); self.assertEqual(len(missing['missing']), 2)

    def test_failed_refresh_restores_sources_and_preserves_failed_output(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'overlays').mkdir(); (root/'analysis').mkdir()
            (root/'overlays/original.cpp').write_text('retained')
            report = dict(accepted_overlays=1)
            def fail(*args):
                (root/'overlays').mkdir(); (root/'overlays/failed.cpp').write_text('diagnostic')
                raise RuntimeError('compiler failed')
            with patch.object(overlays, 'translate_overlays', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'compiler failed'):
                    overlays.refresh_legacy_overlays({}, root, report)
            self.assertEqual((root/'overlays/original.cpp').read_text(), 'retained')
            self.assertEqual(json.loads((root/'analysis/overlay-translation.json').read_text()), report)
            self.assertEqual(len(list((root/'previous-overlays').glob('*/failed-refresh/failed.cpp'))), 1)

    def test_equal_counts_cannot_hide_different_registered_programs(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'overlays').mkdir();(root/'analysis').mkdir()
            (root/'overlays/original.cpp').write_text('retained')
            old=dict(accepted_overlays=1,overlays=[dict(id='o01',registered=True,file='sectors',offset=0,bytes=4096,load_address='0x200000')])
            changed=json.loads(json.dumps(old));changed['overlays'][0]['offset']=4096
            def replace(*args):
                (root/'overlays').mkdir()
                return changed
            with patch.object(overlays,'translate_overlays',side_effect=replace):
                with self.assertRaisesRegex(ValueError,'changed accepted coverage'):
                    overlays.refresh_legacy_overlays({},root,old)
            self.assertEqual((root/'overlays/original.cpp').read_text(),'retained')

    def test_only_header_proven_rebasing_can_change_the_accepted_code_base(self):
        from unittest.mock import patch
        for actual in (0x200000,0x300000):
            with self.subTest(actual=actual), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'overlays').mkdir();(root/'analysis').mkdir()
                (root/'overlays/original.cpp').write_text('retained')
                (root/'analysis/code-census.json').write_text('{}')
                row=dict(id='o01',registered=True,file='level.bin',offset=32,bytes=4096,load_address='0x200010')
                old=dict(accepted_overlays=1,overlays=[row]);new=json.loads(json.dumps(old))
                new['overlays'][0].update(load_address=hex(actual),source_layout={'proof':True})
                def translate(*args):
                    (root/'overlays').mkdir()
                    return new
                with patch.object(overlays,'translate_overlays',side_effect=translate), \
                     patch.object(overlays,'candidates',return_value=([('o01',{})],[])), \
                     patch.object(overlays,'program_image',return_value=dict(base=0x200000,layout={'proof':True})), \
                     patch.object(overlays,'vu0_sync_audit',return_value={'passed':True}):
                    if actual==0x200000:
                        self.assertEqual(overlays.refresh_legacy_overlays({},root,old)['overlays'][0]['load_address'],'0x200000')
                    else:
                        with self.assertRaisesRegex(ValueError,'proven section layout'):
                            overlays.refresh_legacy_overlays({},root,old)
                        self.assertEqual((root/'overlays/original.cpp').read_text(),'retained')


class BranchHandoffClosure(unittest.TestCase):
    def test_incremental_reuse_preserves_only_byte_identical_source_timestamps(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old/generated';new=root/'new/generated'
            old.mkdir(parents=True);new.mkdir(parents=True)
            for name,before,after in [('same.cpp','same','same'),('changed.h','old','new')]:
                (old/name).write_text(before);(new/name).write_text(after)
                os.utime(old/name,ns=(1000000000,1000000000));os.utime(new/name,ns=(2000000000,2000000000))
            self.assertEqual(overlays.reuse_source_timestamps(root/'old',root/'new'),1)
            self.assertEqual((new/'same.cpp').stat().st_mtime_ns,1000000000)
            self.assertEqual((new/'changed.h').stat().st_mtime_ns,2000000000)
            self.assertEqual((new/'changed.h').read_text(),'new')

    def test_branch_decoder_excludes_trap_immediates_reserved_forms_and_unrequested_calls(self):
        self.assertIsNone(overlays.direct_branch_target(0x04080004,0x1000))  # TGEI, not REGIMM branch
        self.assertIsNone(overlays.direct_branch_target(0x18010004,0x1000))  # reserved BLEZ rt
        self.assertIsNone(overlays.direct_branch_target(0x49040004,0x1000))  # unsupported BC2 condition
        self.assertEqual(overlays.direct_branch_target(0x04000004,0x1000),0x1014)
        self.assertIsNone(overlays.direct_branch_target(0x0c000400,0x1000))
        self.assertEqual(overlays.direct_branch_target(0x0c000400,0x1000,include_calls=True),0x1000)

    def test_alternate_return_is_recovered_but_registered_or_external_targets_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            table=root/'register_functions.cpp'
            table.write_text('const uint32_t g_ps2RecompiledFunctionTableBase = 0x1000u;\n'
                             'const uint32_t g_ps2RecompiledFunctionTableEnd = 0x1040u;\n'
                             'g_ps2RecompiledFunctionTable[0] = entry; // 0x1000\n')
            (root/'body.cpp').write_text(
                '// 0x1000: 0x10000003 beq zero, zero, 0x1010\nctx->pc = 0x1010u; return;\n'
                '// 0x1004: 0x10000020 beq zero, zero, external\nctx->pc = 0x1088u; return;\n'
                '// 0x1008: 0x1000fffd beq zero, zero, 0x1000\nctx->pc = 0x1000u; return;\n'
                'ctx->pc = 0x1020u; return; // no decoded direct branch to this address\n')
            self.assertEqual(overlays.branch_handoff_hints(root),[0x1010])
            with table.open('a') as f:f.write('g_ps2RecompiledFunctionTable[4] = alternate; // 0x1010\n')
            self.assertEqual(overlays.branch_handoff_hints(root),[])

if __name__ == '__main__':
    unittest.main()
