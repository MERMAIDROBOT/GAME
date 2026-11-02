"""Disc code census on synthetic images; no game code."""
from pathlib import Path
import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_census

JR_RA, NOP = 0x03E00008, 0


def functions(base, count, words_each=16):
    """`count` functions at `base`: prologue, a jal to the next function, padding, epilogue."""
    out = []
    for i in range(count):
        callee = base + ((i + 1) % count) * words_each * 4
        body = [0x27BDFFF0, 0xFFBF0000, 0x0C000000 | (callee >> 2) & 0x03FFFFFF, NOP]
        body += [0x24020000 | i & 0x7FFF] * (words_each - len(body) - 4)
        body += [0xDFBF0000, JR_RA, 0x27BD0010, NOP]
        out += body
    return struct.pack(f'<{len(out)}I', *out)


def noise(size, seed=b'census'):
    data, block = bytearray(), seed
    while len(data) < size:
        block = hashlib.sha256(block).digest()
        data += block
    return bytes(data[:size])


class Windows(unittest.TestCase):
    def test_code_region_is_found_and_based_exactly(self):
        base = 0x00300000
        code = functions(base, 2048)  # 128 KiB
        image = noise(256 * 1024) + code + noise(192 * 1024, b'tail')
        stream = io.BytesIO(image)
        regions = code_census.merge(code_census.code_windows(stream, len(image)), len(image))
        self.assertEqual(regions, [(256 * 1024, 256 * 1024 + len(code))])
        load, votes, sampled = code_census.infer_base(code)
        self.assertEqual(load, base)
        self.assertGreater(votes, sampled // 2)

    def test_noise_has_no_code_windows(self):
        image = noise(1024 * 1024)
        self.assertEqual(code_census.code_windows(io.BytesIO(image), len(image)), [])

    def test_iop_content_is_recognised_without_the_irx_name(self):
        irx = b'\x7fELF' + bytes(12) + struct.pack('<H', 0xFF80) + bytes(46)
        self.assertTrue(code_census._is_iop('DRIVER.BIN', irx))
        self.assertTrue(code_census._is_iop('IOPRP243.IMG;1', b'RESET\x00' + bytes(58)))
        self.assertFalse(code_census._is_iop('GAME.ELF', b'\x7fELF' + bytes(12) + struct.pack('<H', 2) + bytes(46)))

    def test_label_uses_the_file_holding_most_of_a_window_aligned_region(self):
        files = {'IOPRP.IMG': (0, 300 * 1024), 'OTHER.BIN': (300 * 1024, 310 * 1024)}
        self.assertEqual(code_census._label(256 * 1024, 320 * 1024, files), 'IOPRP.IMG')
        self.assertIsNone(code_census._label(512 * 1024, 576 * 1024, files))
        # A small file touching a large hidden region does not claim it.
        self.assertIsNone(code_census._label(300 * 1024, 1324 * 1024, files))

    def test_base_is_none_without_calls(self):
        self.assertEqual(code_census.infer_base(noise(4096))[0], None)


class Census(unittest.TestCase):
    def test_disc_files_are_labelled_and_the_boot_executable_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            root = private / 'disc-root'; root.mkdir()
            (root / 'BOOT.ELF').write_bytes(functions(0x100000, 1024))
            (root / 'LEVEL.BIN').write_bytes(noise(64 * 1024) + functions(0x400000, 1024))
            (root / 'DRIVER.IRX').write_bytes(functions(0x0, 1024))
            (root / 'MOVIE.PSS').write_bytes(noise(128 * 1024))
            (root / 'IOPRP.IMG').write_bytes(b'RESET\x00' + bytes(58) + functions(0x0, 1024))
            report = code_census.census(private, {'id': 'synthetic', 'expected_executable': 'BOOT.ELF'})
            by_file = {r['file']: r for r in report['regions']}
            self.assertEqual(set(by_file), {'LEVEL.BIN', 'DRIVER.IRX', 'IOPRP.IMG'})
            self.assertEqual(by_file['IOPRP.IMG']['kind'], 'iop_module')
            self.assertEqual(by_file['LEVEL.BIN']['kind'], 'ee_code_candidate')
            self.assertEqual(by_file['LEVEL.BIN']['estimated_load_address'], '0x00400000')
            self.assertEqual(by_file['DRIVER.IRX']['kind'], 'iop_module')
            self.assertEqual(report['ee_candidate_bytes'], 64 * 1024)
            self.assertFalse(report['translation_input'])
            saved = json.loads((private / 'analysis/code-census.json').read_text())
            self.assertEqual(saved['ee_candidate_regions'], 1)


    def test_sector_image_labels_code_outside_every_file_as_unlisted(self):
        from test_cdimage import synthetic_iso
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            raw, files = synthetic_iso()
            image = raw + bytes(-len(raw) % code_census.WINDOW) + functions(0x500000, 1024)  # hidden program
            (private / 'cd-image').mkdir(); (private / 'cd-image/disc.iso').write_bytes(image)
            root = private / 'disc-root'; root.mkdir()
            (root / 'SYSTEM.CNF').write_bytes(files['SYSTEM.CNF'])
            (root / 'TEST.ELF').write_bytes(b'\x7fELF' + bytes(60))
            report = code_census.census(private, {'id': 'synthetic'})
            self.assertEqual(report['scanned'], 'sector image')
            self.assertEqual([(r['file'], r['estimated_load_address']) for r in report['regions']],
                             [('unlisted_sectors', '0x00500000')])
            self.assertEqual(report['unlisted_sector_bytes'], code_census.WINDOW)


if __name__ == '__main__':
    unittest.main()
