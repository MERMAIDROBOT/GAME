"""match-function: relocatable objects parse into functions and relocations; comparison masks only the fields
relocations fill; compilers count only when their archive matches the pinned digest. Synthetic, no game data;
the container compile runs only when Docker and a pinned compiler are present."""
from pathlib import Path
import hashlib
import shutil
import struct
import subprocess
import tempfile
import unittest

import match_function as m
import pipeline


def rel_object(words, relocs, name=b'f'):
    text = b''.join(struct.pack('<I', w) for w in words)
    names = b'\0.text\0.rel.text\0.symtab\0.strtab\0.shstrtab\0'
    strtab = b'\0' + name + b'\0'
    symtab = bytes(16) + struct.pack('<IIIBBH', 1, 0, len(text), 0x12, 0, 1)
    rel = b''.join(struct.pack('<II', o, t) for o, t in relocs)
    out = bytearray(52)
    offs = {}
    for key, data in [('text', text), ('rel', rel), ('sym', symtab), ('str', strtab), ('shs', names)]:
        while len(out) % 4: out += b'\0'
        offs[key] = len(out); out += data
    shoff = len(out)
    def n(x): return names.index(x + b'\0')
    sh = [[0] * 10,
          [n(b'.text'), 1, 6, 0, offs['text'], len(text), 0, 0, 4, 0],
          [n(b'.rel.text'), 9, 0, 0, offs['rel'], len(rel), 3, 1, 4, 8],
          [n(b'.symtab'), 2, 0, 0, offs['sym'], len(symtab), 4, 1, 4, 16],
          [n(b'.strtab'), 3, 0, 0, offs['str'], len(strtab), 0, 0, 1, 0],
          [n(b'.shstrtab'), 3, 0, 0, offs['shs'], len(names), 0, 0, 1, 0]]
    for h in sh: out += struct.pack('<10I', *h)
    struct.pack_into('<16sHHIIIIIHHHHHH', out, 0, b'\x7fELF\x01\x01\x01' + bytes(9), 1, 8, 1, 0, 0, shoff, 0,
                     52, 0, 0, 40, len(sh), 5)
    return bytes(out)


class Objects(unittest.TestCase):
    def test_parse_and_mask(self):
        jal, lui = 0x0c000000, 0x3c020000
        obj = rel_object([jal, lui, 0x03e00008, 0], [(0, 4), (4, 5)])
        sections, functions, relocations = m.parse_object(obj)
        self.assertEqual(functions['f'], ('.text', 0, 16))
        self.assertEqual(relocations['.text'], [(0, 4), (4, 5)])
        original = struct.pack('<4I', 0x0c0f3df2, 0x3c020051, 0x03e00008, 0)
        same, rows = m.compare(original, sections['.text'], relocations['.text'])
        self.assertTrue(same)
        changed = struct.pack('<4I', 0x0c0f3df2, 0x3c030051, 0x03e00008, 0)   # different register: not masked
        self.assertFalse(m.compare(changed, sections['.text'], relocations['.text'])[0])
        self.assertFalse(m.compare(original + bytes(4), sections['.text'], relocations['.text'])[0])

    def test_compilers_need_pinned_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'downloads').mkdir(); (root / 'extracted/ee-gcc-x/bin').mkdir(parents=True)
            (root / 'extracted/ee-gcc-x/bin/ee-gcc').write_text('')
            archive = root / 'downloads/ee-gcc-x.tar.xz'; archive.write_bytes(b'archive')
            self.assertEqual(m.compilers(root), {})
            (root / 'SHA256SUMS').write_text(hashlib.sha256(b'archive').hexdigest() + '  ' + str(archive) + '\n')
            self.assertIn('ee-gcc-x', m.compilers(root))
            archive.write_bytes(b'changed')
            self.assertEqual(m.compilers(root), {})

    def test_packaged(self):
        for name in ('match_function.py', 'tests/test_match_function.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


def _available():
    try:
        return bool(shutil.which('docker')) and subprocess.run(['docker', 'info'], capture_output=True, timeout=20).returncode == 0 \
            and 'ee-gcc2.96' in m.compilers()
    except Exception:
        return False


@unittest.skipUnless(_available(), 'Docker and the pinned ee-gcc2.96 compiler required')
class Container(unittest.TestCase):
    def test_compiles_without_network(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            source = Path(tmp) / 'f.c'
            source.write_text('int f(int a) { return a + 1; }\n')
            obj = m.compile_source(m.compilers()['ee-gcc2.96'], source, Path(tmp) / 'w')
            sections, functions, _ = m.parse_object(obj)
            self.assertIn('f', functions)
            self.assertIn('addiu', m._text(struct.unpack_from('<I', sections['.text'], 0)[0]) +
                          m._text(struct.unpack_from('<I', sections['.text'], 4)[0]))


if __name__ == '__main__':
    unittest.main()
