"""rebuild-binary: an executable split into assembly rebuilds byte-for-byte; the assembly text is the source of the
loaded bytes (an edited instruction changes exactly its word); references become labels (%hi/%lo, %gp_rel, branch,
call, data pointers); gp comes from the startup code; blobs and outputs are guarded. Synthetic ELF, no game data."""
from pathlib import Path
import struct
import tempfile
import unittest

import binary_rebuild as rebuild
import isa_r5900 as isa
import pipeline

GP = 0x00108000


def enc(name, **values):
    for spec in isa.BY_NAME[name]:
        if set(spec.operands) | set(spec.suffix) == set(values):
            return isa.encode(spec, values)
    raise KeyError(name)


def r(name):
    return isa.GPR_INDEX[name]


def text_words():
    data = 0x00101000
    return [
        enc('lui', rt=r('a0'), uimm=(GP + 0x8000) >> 16),
        enc('addiu', rt=r('a0'), rs=r('a0'), simm=GP & 0xffff),
        enc('or', rd=r('gp'), rs=r('a0'), rt=0),
        enc('addiu', rt=r('v1'), rs=0, simm=0x3c),
        enc('syscall', code=0),
        enc('lui', rt=r('v0'), uimm=(data + 4 + 0x8000) >> 16),
        enc('lw', rt=r('v0'), mem=((data + 4) & 0xffff, r('v0'))),
        enc('beq', rs=r('v0'), rt=0, br=2),
        0,
        enc('jal', jt=(0x00100040 >> 2)),
        0,
        enc('lw', rt=r('t0'), mem=((0x00107ff8 - GP) & 0xffff, r('gp'))),
        enc('jr', rs=r('ra')),
        0,
        0xec000000,                                    # unused opcode 0x3b: not an instruction: kept as a data word
        0,
        enc('jr', rs=r('ra')),                         # 0x100040: called function
        0,
    ]


def make_elf():
    text = b''.join(struct.pack('<I', w) for w in text_words())
    data = struct.pack('<4I', 0x00100040, 0x00101008, 0x12345678, 0x00101010) + b'hi\0\0'
    sdata = struct.pack('<2I', 7, 0x00108010)                       # at 0x107ff8, gp-relative
    comment = b'synthetic test build\0'
    names = b'\0.text\0.data\0.sdata\0.bss\0.comment\0.shstrtab\0'
    def name(n): return names.index(n.encode() + b'\0')
    layout = [  # name, type, flags, addr, bytes / size
        ('.text', 1, 6, 0x00100000, text), ('.data', 1, 3, 0x00101000, data), ('.sdata', 1, 3, 0x00107ff8, sdata),
        ('.bss', 8, 3, 0x00108000, 0x40), ('.comment', 1, 0, 0, comment), ('.shstrtab', 3, 0, 0, names)]
    out = bytearray(0x1000)
    headers = [[0] * 10]
    for n, kind, flags, addr, content in layout:
        if kind == 8:
            headers.append([name(n), kind, flags, addr, len(out), content, 0, 0, 16, 0]); continue
        while len(out) % 16: out += b'\xaa'                         # nonzero padding must survive too
        headers.append([name(n), kind, flags, addr, len(out), len(content), 0, 0, 16, 0])
        out += content
    while len(out) % 4: out += b'\0'
    shoff = len(out)
    for h in headers:
        out += struct.pack('<10I', *h)
    loaded_end = headers[3][4] + headers[3][5]
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    struct.pack_into(rebuild.EHDR, out, 0, ident, 2, 8, 1, 0x00100000, 52, shoff, 0x20924001, 52, 32, 1, 40,
                     len(headers), len(headers) - 1)
    struct.pack_into('<8I', out, 52, 1, 0x1000, 0x00100000, 0x00100000, loaded_end - 0x1000,
                     0x00108040 - 0x00100000, 7, 16)
    return bytes(out)


class Roundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / 'split'
        self.raw = make_elf()

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_with_symbolic_references(self):
        report = rebuild.roundtrip(self.raw, self.out)
        self.assertTrue(report['identical'], report)
        self.assertEqual(rebuild.crt0_gp(self.raw, rebuild.parse_elf(self.raw)), GP)
        for key in ('hilo', 'branch', 'jump', 'gp_rel'):
            self.assertGreaterEqual(report[key], 1, key)
        self.assertGreaterEqual(report['data_pointer'], 3)
        self.assertEqual(report['undecoded'], 1)
        text = ''.join(p.read_text() for p in sorted((self.out / 'asm').rglob('*.s')))
        for fragment in ('%hi(D_00101004)', '%lo(D_00101004)(v0)', 'jal func_00100040', '%gp_rel(D_00107FF8)(gp)',
                         '.word func_00100040', '.word D_00101008', 'D_00108010 = 0x108010', '.word 0xec000000'):
            self.assertIn(fragment, text)

    def test_assembly_is_the_source(self):
        rebuild.split(self.raw, self.out)
        path = next(p for p in (self.out / 'asm').rglob('*.s') if 'addiu v1, zero, 60' in p.read_text())
        path.write_text(path.read_text().replace('addiu v1, zero, 60', 'addiu v1, zero, 61'))
        rebuilt = rebuild.assemble(self.out)
        changed = [i for i in range(len(self.raw)) if self.raw[i] != rebuilt[i]]
        self.assertEqual(changed, [0x1000 + 3 * 4])

    def test_moved_label_moves_references(self):
        rebuild.split(self.raw, self.out)
        data = next(p for p in (self.out / 'asm').rglob('*data.s') if 'D_00101008' in p.read_text())
        lines = data.read_text().splitlines()
        i = lines.index('D_00101008:')
        lines[i], lines[i + 1] = lines[i + 1], lines[i]             # label one word later: 0x10100c
        data.write_text('\n'.join(lines) + '\n')
        rebuilt = rebuild.assemble(self.out)
        data_offset = rebuild.parse_elf(self.raw)['shdrs'][2][4]
        self.assertEqual(struct.unpack_from('<I', rebuilt, data_offset + 4)[0], 0x0010100c)

    def test_guards(self):
        rebuild.split(self.raw, self.out)
        with self.assertRaises(ValueError):
            rebuild.split(self.raw, self.out)                         # never overwrite a split
        blob = next((self.out / 'blobs').glob('*.bin'))
        blob.write_bytes(b'x' * blob.stat().st_size)
        with self.assertRaises(ValueError):
            rebuild.assemble(self.out)

    def test_packaged(self):
        for name in ('isa_r5900.py', 'binary_rebuild.py', 'tests/test_isa_r5900.py', 'tests/test_binary_rebuild.py',
                     'MATCHING.md'):
            self.assertIn(name, pipeline.SOURCE_FILES)


if __name__ == '__main__':
    unittest.main()
