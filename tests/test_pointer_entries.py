"""Pointer-only function start scan on a synthetic MIPS ELF; no game code."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pointer_entries

JR_RA, NOP = 0x03E00008, 0
CODE = [
    0x27BDFFF0, JR_RA, NOP,               # 0x100000 f1: addiu sp,sp,-16 prologue
    0x34020001, JR_RA, 0x27BD0010,        # 0x10000c f2: after jr ra + delay slot (delay slot not a nop)
    NOP, NOP,                             # 0x100018 alignment padding
    0x0000102D, JR_RA, NOP,               # 0x100020 f3: after jr ra + delay + two padding nops
    0x3C040010, 0x24840020, JR_RA, NOP,   # 0x10002c f4: lui a0,0x10 ; addiu a0,a0,0x20 -> f3 (code pointer)
]
DATA = [0x100000, 0x10000C, 0x10002C, 0x100014, 0x100030, 0x200000, 0x100002]


def elf():
    code = struct.pack(f'<{len(CODE)}I', *CODE)
    data = struct.pack(f'<{len(DATA)}I', *DATA)
    header = struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF\x01\x01\x01' + bytes(9), 2, 8, 1, 0x100000, 52, 0, 0, 52, 32, 2, 40, 0, 0)
    text = struct.pack('<8I', 1, 0x100, 0x100000, 0, len(code), len(code), 5, 4)
    rodata = struct.pack('<8I', 1, 0x200, 0x200000, 0, len(data), len(data), 4, 4)
    raw = bytearray(0x200 + len(data))
    raw[:52] = header; raw[52:84] = text; raw[84:116] = rodata
    raw[0x100:0x100 + len(code)] = code; raw[0x200:] = data
    return bytes(raw)


class PointerEntryScan(unittest.TestCase):
    def test_accepts_returns_and_prologues_only(self):
        found = dict(pointer_entries.scan(elf()))
        self.assertEqual(found, {0x100000: 'stack_prologue', 0x10000C: 'after_return',
                                 0x100020: 'after_return', 0x10002C: 'after_return'})
        # A delay slot, a mid-function word, a data address and a misaligned word are never hints.
        for rejected in (0x100014, 0x100030, 0x200000, 0x100002):
            self.assertNotIn(rejected, found)

    def test_lui_pair_is_dropped_when_register_is_overwritten(self):
        code = list(CODE)
        code[12] = 0x24040000  # addiu a0,zero,0 overwrites a0 ...
        code[13] = 0x24840020  # ... before addiu a0,a0,0x20 would complete the pair
        raw = bytearray(elf())
        struct.pack_into(f'<{len(code)}I', raw, 0x100, *code)
        pointers = pointer_entries._pointers(bytes(raw), pointer_entries.executable_spans(bytes(raw)))
        self.assertNotIn(0x100020, pointers)

    def test_apply_merges_new_hints_and_keeps_analyzer_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            (private / 'disc-root').mkdir(); (private / 'analysis').mkdir(); (private / 'generated').mkdir()
            (private / 'disc-root/TEST.ELF').write_bytes(elf())
            config = '[general]\ninput = "x"\nentry_points = ["known@0x00100000"]\n\n[mmio]\n'
            (private / 'analysis/recomp.toml').write_text(config)
            (private / 'generated/sub_0010000C_0x10000c.cpp').write_text('')
            report = pointer_entries.apply({'id': 'synthetic', 'expected_executable': 'TEST.ELF'}, private)
            self.assertEqual(report['already_known'], 2)
            self.assertEqual([c['address'] for c in report['candidates']], ['00100020', '0010002c'])
            entries = tomllib.loads((private / 'analysis/recomp.toml').read_text())['general']['entry_points']
            self.assertEqual(entries, ['known@0x00100000', 'ptr_00100020@0x00100020', 'ptr_0010002C@0x0010002C'])
            self.assertEqual((private / 'analysis/recomp.toml.analyzer-original').read_text(), config)
            saved = json.loads((private / 'analysis/pointer-entry-hints.json').read_text())
            self.assertFalse(saved['functions_verified'])
            # Re-running is idempotent and never replaces the analyzer original.
            again = pointer_entries.apply({'id': 'synthetic', 'expected_executable': 'TEST.ELF'}, private)
            self.assertEqual(again['candidates'], [])
            self.assertEqual((private / 'analysis/recomp.toml.analyzer-original').read_text(), config)

    def test_report_only_leaves_config_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            (private / 'disc-root').mkdir(); (private / 'analysis').mkdir()
            (private / 'disc-root/TEST.ELF').write_bytes(elf())
            (private / 'analysis/recomp.toml').write_text('[general]\ninput = "x"\n')
            report = pointer_entries.apply({'id': 'synthetic', 'expected_executable': 'TEST.ELF'}, private, write_config=False)
            self.assertEqual(len(report['candidates']), 4)
            self.assertFalse(report['config_updated'])
            self.assertEqual((private / 'analysis/recomp.toml').read_text(), '[general]\ninput = "x"\n')
            self.assertFalse((private / 'analysis/recomp.toml.analyzer-original').exists())

    def test_requires_analyzer_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'analyze first'):
                pointer_entries.apply({'id': 'synthetic'}, Path(tmp))


if __name__ == '__main__':
    unittest.main()
