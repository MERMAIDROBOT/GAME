"""Shape-matched SDK accessors bind only as complete, agreeing pairs."""
import struct
import tomllib
import unittest

import overlays
import sdk_shapes

BASE = 0x100000
JR = 0x03E00008


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def r_type(rs, rt, rd, sa, fn):
    return (rs << 21) | (rt << 16) | (rd << 11) | (sa << 6) | fn


def sif_pair(hi, lo, set_hi=None):
    prefix = lambda h: [i_type(0x0F, 0, 2, h), r_type(0, 4, 4, 2, 0), i_type(0x09, 2, 2, lo), r_type(4, 2, 4, 0, 0x21)]
    get = prefix(hi) + [JR, i_type(0x23, 4, 2, 0)]
    put = prefix(hi if set_hi is None else set_hi) + [r_type(5, 0, 2, 0, 0x2D), JR, i_type(0x2B, 4, 5, 0), 0]
    return get + put


def mpeg_pair(offset=0x40, second_offset=4):
    first = [i_type(0x23, 4, 3, offset), JR, i_type(0x23, 3, 2, 0), 0]
    second = [i_type(0x23, 4, 3, offset), i_type(0x23, 3, 2, second_offset), JR, i_type(0x0B, 2, 2, 1)]
    return first + second


def elf(words):
    code = struct.pack(f'<{len(words)}I', *words)
    return overlays.wrap_elf(code, BASE)


class SdkShapes(unittest.TestCase):
    def test_pairs_bind_at_their_starts(self):
        padding = [0] * 4
        found = sdk_shapes.find(elf(padding + sif_pair(0x53, 0xBE00) + padding + mpeg_pair()))
        sif = BASE + 16
        mpeg = sif + (6 + 8) * 4 + 16
        self.assertEqual(found, {'sceSifGetSreg': sif, 'sceSifSetSreg': sif + 24,
                                 'sceMpegIsEnd': mpeg, 'sceMpegIsRefBuffEmpty': mpeg + 16})

    def test_disagreeing_or_lone_shapes_never_bind(self):
        self.assertEqual(sdk_shapes.find(elf(sif_pair(0x53, 0xBE00, set_hi=0x54))), {})
        self.assertEqual(sdk_shapes.find(elf(mpeg_pair()[:4] + [0] * 8)), {})
        self.assertEqual(sdk_shapes.find(elf(mpeg_pair(second_offset=8))), {})

    def test_duplicate_matches_are_dropped(self):
        self.assertEqual(sdk_shapes.find(elf(mpeg_pair() + mpeg_pair())), {})

    def test_stub_merge_preserves_other_settings(self):
        text = ('[general]\ninput = "x"\nstubs = [\n  # comment with ] bracket\n  "a@0x1",\n]\n'
                'entry_points = ["e@0x2"]\n\n[other]\nk = 1\n')
        merged = tomllib.loads(sdk_shapes.with_stubs(text, ['b@0x00000003', 'a@0x1']))
        self.assertEqual(merged['general']['stubs'], ['a@0x1', 'b@0x00000003'])
        self.assertEqual(merged['general']['entry_points'], ['e@0x2'])
        self.assertEqual(merged['other'], {'k': 1})


if __name__ == '__main__':
    unittest.main()
