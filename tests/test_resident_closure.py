"""Original instruction bytes, ownership and callable coverage; no retail data."""
import struct
import unittest
from resident_closure import direct_targets

class Closure(unittest.TestCase):
    def collect(self, target=0x1100, covered=(), code=None, pc=0x2000, spans=None, word=None):
        word = (0x0C000000 | target >> 2) if word is None else word
        text = f'// 0x{pc:x}: 0x{word:08x} jal 0x{target:x}\n'
        return direct_targets(struct.pack('<I', word) if code is None else code,
                              0x2000, [(0x2000, 0x2004)] if spans is None else spans,
                              [text], [(0x1000, 0x1800, 0)], set(covered))
    def test_only_unregistered_resident_executable_targets(self):
        self.assertEqual(self.collect(), {0x1100})
        self.assertEqual(self.collect(covered={0x1100}), set())
        self.assertEqual(self.collect(target=0x1800), set())
        self.assertEqual(self.collect(target=0x2000), set())
        self.assertEqual(self.collect(word=0x24041100), set())
    def test_original_word_and_source_executable_span_required(self):
        with self.assertRaisesRegex(ValueError, 'original disc bytes'):self.collect(code=bytes(4))
        with self.assertRaisesRegex(ValueError, 'outside accepted'):self.collect(spans=[])
        with self.assertRaisesRegex(ValueError, 'outside accepted'):self.collect(pc=0x2004)
    def test_overlay_ownership_wins_even_when_resident_span_overlaps(self):
        word = 0x0C000800
        self.assertEqual(direct_targets(struct.pack('<I', word), 0x2000, [(0x2000, 0x2004)],
                         ['// 0x2000: 0x0c000800 jal'], [(0x1000, 0x3000, 0)], set()), set())

class StableConfiguration(unittest.TestCase):
    def test_retranslation_keeps_proven_entries_even_when_current_table_covers_them(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import resident_closure
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'analysis').mkdir();config=root/'base.toml'
            config.write_text('[general]\ninput="synthetic.elf"\n')
            with patch('resident_closure.discover', return_value=dict(entries=[],all_entries=['00001100'])):
                result,_=resident_closure.configuration({},root,config)
            self.assertIn('overlay_resident_00001100@0x00001100',result.read_text())
            self.assertNotIn('entry_points',config.read_text())
