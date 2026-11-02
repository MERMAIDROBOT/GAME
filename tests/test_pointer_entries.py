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


def elf_with_sections():
    """Three sections: executable (nop, nop, prologue ...), executable non-EE (VU-like word), data (prologue-shaped word)."""
    exec_words = [NOP, NOP, 0x27BDFF40, 0x7FBF0000, JR_RA, 0x27BD00C0]
    vu_words = [0x8000033C, 0x000002FF]   # not a MIPS prologue
    data_words = [0x27BDFFF0, 0x00000001]  # looks like a prologue but SHF_EXECINSTR is clear
    blobs = [(0x300000, exec_words, 6), (0x310000, vu_words, 6), (0x320000, data_words, 3)]
    raw = bytearray(0x100)
    raw[:52] = struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF\x01\x01\x01' + bytes(9), 2, 8, 1, 0x300000, 52, 0, 0, 52, 32, 1, 40, 0, 0)
    raw[52:84] = struct.pack('<8I', 1, 0x100, 0x300000, 0, 4 * len(exec_words), 4 * len(exec_words), 5, 4)  # PT_LOAD of the code
    headers = [bytes(40)]
    for addr, words, flags in blobs:
        offset = len(raw)
        raw += struct.pack(f'<{len(words)}I', *words)
        headers.append(struct.pack('<10I', 0, 1, flags, addr, offset, 4 * len(words), 0, 0, 4, 0))
    shoff = len(raw)
    raw += b''.join(headers)
    struct.pack_into('<I', raw, 32, shoff)
    struct.pack_into('<HH', raw, 46, 40, len(headers))
    return bytes(raw)


class SectionStartEntries(unittest.TestCase):
    def test_prologue_after_padding_at_executable_section_start_is_a_candidate(self):
        self.assertEqual(pointer_entries._section_starts(elf_with_sections()), {0x300008})

    def test_scan_reports_section_start_reason(self):
        self.assertIn((0x300008, 'section_start'), pointer_entries.scan(elf_with_sections()))

    def test_elf_without_section_headers_adds_nothing(self):
        self.assertEqual(pointer_entries._section_starts(elf()), set())


class AnalyzeStageAddsPointerHints(unittest.TestCase):
    """The analyze stage merges pointer-entry hints itself (no manual step); profiles may opt out."""

    def run_analyze(self, profile):
        from unittest import mock
        import pipeline
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        private = Path(tmp.name)
        (private / 'disc-root').mkdir(); (private / 'analysis').mkdir()
        elf_path = private / 'disc-root/TEST.ELF'; elf_path.write_bytes(elf())
        config = '[general]\ninput = "x"\nentry_points = ["known@0x00100000"]\n\n[mmio]\n'
        def analyzer(command, log, **kwargs):
            (private / 'analysis/recomp.toml').write_text(config)
        with mock.patch.object(pipeline, 'inspect', return_value=elf_path), mock.patch.object(pipeline, 'run', side_effect=analyzer):
            pipeline.analyze(profile, private)
        stage = json.loads((private / 'analysis/analyzer-stage.json').read_text())
        return private, stage, config

    def test_hints_are_merged_by_default(self):
        private, stage, config = self.run_analyze({'id': 'synthetic', 'expected_executable': 'TEST.ELF'})
        entries = tomllib.loads((private / 'analysis/recomp.toml').read_text())['general']['entry_points']
        self.assertIn('ptr_00100020@0x00100020', entries)
        self.assertEqual(stage['pointer_entry_hints']['candidates'], 3)
        self.assertTrue(stage['pointer_entry_hints']['config_updated'])

    def test_profile_can_opt_out(self):
        private, stage, config = self.run_analyze({'id': 'synthetic', 'expected_executable': 'TEST.ELF', 'pointer_entry_hints': False})
        self.assertIsNone(stage['pointer_entry_hints'])
        entries = tomllib.loads((private / 'analysis/recomp.toml').read_text())['general']['entry_points']
        self.assertEqual(entries, ['known@0x00100000'])


if __name__ == '__main__':
    unittest.main()
