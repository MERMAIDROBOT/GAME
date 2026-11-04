"""R5900 instruction table: every spec accounts for all 32 bits, no two specs match the same word, decoded words
re-encode exactly (binary and through assembly text), and known encodings decode to the expected instructions."""
import random
import unittest

import isa_r5900 as isa
import binary_rebuild as rebuild


def text_roundtrip(word, pc=0x100000):
    spec, values = isa.decode(word)
    operands = [rebuild._operand_text(spec, values, o, None) for o in spec.operands]
    return rebuild.Assembler(None).instruction(isa.mnemonic_text(spec, values), operands, pc)


class Table(unittest.TestCase):
    def test_no_overlapping_specs(self):
        self.assertEqual(isa.overlaps(), [])
        self.assertGreater(len(isa.TABLE), 380)

    def test_spec_must_cover_all_bits(self):
        with self.assertRaises(ValueError):
            isa.Spec('broken', {'op': 0, 'funct': 0x21}, ('rd', 'rs', 'rt'), ())   # sa field unaccounted

    def test_random_words_roundtrip(self):
        rng = random.Random(5900)
        decoded = 0
        for _ in range(200000):
            word = rng.getrandbits(32)
            row = isa.decode(word)
            if row:
                decoded += 1
                self.assertEqual(isa.encode(*row), word, hex(word))
        self.assertGreater(decoded, 20000)

    def test_text_roundtrip_every_spec(self):
        rng = random.Random(2)
        for spec in isa.TABLE:
            for _ in range(20):
                word = (rng.getrandbits(32) & ~spec.mask) | spec.match
                self.assertEqual(text_roundtrip(word), word, f'{spec.mnemonic} {word:#010x}')


class KnownEncodings(unittest.TestCase):
    CASES = {
        0x27bdffe0: 'addiu sp, sp, -32', 0x03e00008: 'jr ra', 0x7fbf0000: 'sq ra, 0(sp)',
        0x3c040016: 'lui a0, 0x16', 0x2403003c: 'addiu v1, zero, 60', 0x0000000c: 'syscall 0',
        0x46000800: 'add.s f0, f1, f0', 0x4be1006a: None, 0x70a41489: None, 0x0000102d: 'daddu v0, zero, zero',
    }

    def test_known(self):
        for word, text in self.CASES.items():
            row = isa.decode(word)
            self.assertIsNotNone(row, hex(word))
            spec, values = row
            shown = f'{isa.mnemonic_text(spec, values)} ' + ', '.join(
                rebuild._operand_text(spec, values, o, None) for o in spec.operands)
            if text:
                self.assertEqual(shown.strip(), text)

    def test_vu0_and_mmi_names(self):
        self.assertEqual(isa.decode(0x4be1006a)[0].mnemonic, "vmul")     # vmul.xyzw vf1, vf0, vf1: funct 0x2a
        self.assertEqual(isa.decode(0x70a41489)[0].mnemonic, 'pand')     # MMI2 sa 0x12
        self.assertEqual(isa.decode(0x4a0003bf)[0].mnemonic, 'vwaitq')   # special2 0x3b


if __name__ == '__main__':
    unittest.main()
