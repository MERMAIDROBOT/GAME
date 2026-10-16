"""Synthetic controls for executable knowledge; no game bytes or network."""
from pathlib import Path
import copy
import json
import struct
import tempfile
import unittest
import knowledge as k


def module():
    payload=struct.pack('<IIHH8s',0x41e00000,0,0x101,0,b'thbase\0\0')
    payload+=struct.pack('<4I',0x03e00008,0x24000004,0,0)
    raw=bytearray(52+40+len(payload))
    raw[:6]=b'\x7fELF\x01\x01';struct.pack_into('<H',raw,18,8)
    struct.pack_into('<I',raw,32,52);struct.pack_into('<HH',raw,46,40,1)
    struct.pack_into('<10I',raw,52,0,1,2,0,92,len(payload),0,0,4,0)
    raw[92:]=payload
    return raw


class KnowledgeTests(unittest.TestCase):
    def test_all_sqrt_register_fields_from_opcode_not_disassembly_label(self):
        rule=k.load()['rules'][0]
        for ft in range(32):
            for fs in range(32):
                for fd in [0,1,17,31]:
                    word=0x46000004 | ft<<16 | fs<<11 | fd<<6
                    before=f'ctx->f[{fd}] = FPU_SQRT_S(ctx->f[{fs}]);'
                    self.assertEqual(k.expression(rule,word,before),f'ctx->f[{fd}] = FPU_SQRT_S(ctx->f[{ft}]);')
    def test_wrong_opcode_and_unexpected_register_are_rejected(self):
        rule=k.load()['rules'][0]
        with self.assertRaises(ValueError): k.expression(rule,0x460c0045,'ctx->f[1] = FPU_SQRT_S(ctx->f[0]);')
        with self.assertRaises(ValueError): k.expression(rule,0x460c0044,'ctx->f[2] = FPU_SQRT_S(ctx->f[0]);')
    def test_invalid_file_blocks_all_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.cpp'; b=Path(tmp)/'b.cpp'
            a.write_text('// 0x1000: 0x460c0044 wrong-label\nctx->f[1] = FPU_SQRT_S(ctx->f[0]);\n')
            b.write_text('ctx->f[1] = FPU_SQRT_S(ctx->f[0]);\n')
            old=a.read_bytes()
            with self.assertRaises(ValueError): k.apply_translation(tmp)
            self.assertEqual(a.read_bytes(),old)
    def test_title_rule_requires_matching_revision(self):
        r=copy.deepcopy(k.load()['rules'][0]); r.update(scope='title',executable_sha256=['a'*64])
        self.assertFalse(k.eligible(r,'b'*64));self.assertTrue(k.eligible(r,'a'*64))
        r.pop('executable_sha256')
        with self.assertRaisesRegex(ValueError,'revisions'): k.validate(dict(schema=1,revision=1,rules=[r]))
    def test_conflicting_rules_and_bad_fixture_cannot_load(self):
        bank=k.load(); new=copy.deepcopy(bank['rules'][0]);new['id']='conflicting'
        bank['rules'].append(new)
        with self.assertRaisesRegex(ValueError,'Overlapping'): k.validate(bank)
        bank=k.load();bank['rules'][0]['fixtures'][0]['after']='incorrect'
        with self.assertRaisesRegex(ValueError,'fixture failed'): k.validate(bank)
    def test_append_preserves_history_and_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            bankpath=Path(tmp)/'bank.json';bankpath.write_text(json.dumps(k.load()))
            candidate=copy.deepcopy(k.load()['rules'][0]);candidate.update(id='future-rule',status='proposed')
            path=Path(tmp)/'candidate.json';path.write_text(json.dumps(candidate))
            before=bankpath.read_bytes();result=k.add_rule(bankpath,path)
            self.assertEqual(result['revision'],2)
            self.assertEqual(next((Path(tmp)/'knowledge-history').iterdir()).read_bytes(),before)
            with self.assertRaisesRegex(ValueError,'already exists'): k.add_rule(bankpath,path)
    def test_import_contract_bounds_and_ordinal(self):
        raw=module(); rows=k.import_tables(raw)
        self.assertEqual(rows,[dict(library='thbase',version=0x101,ordinals=[4],file_offset=92)])
        struct.pack_into('<I',raw,92+20+4,0xdead0004)
        self.assertEqual(k.import_tables(raw),[])
        with self.assertRaises(ValueError): k.import_tables(bytes(52))
    def test_unterminated_import_table_is_not_accepted(self):
        raw=module();struct.pack_into('<II',raw,len(raw)-8,0x03e00008,0x24000005)
        self.assertEqual(k.import_tables(raw),[])
    def test_memory_fixture_detects_same_class_of_heap_collision(self):
        bank=k.load();bank['rules'][1]['fixtures'][0]['conflicts']=0
        with self.assertRaises(ValueError): k.validate(bank)
