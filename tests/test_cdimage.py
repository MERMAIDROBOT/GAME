"""Sector-image intake on a synthetic ISO9660 volume; no disc data."""
from pathlib import Path
from unittest import mock
import hashlib
import json
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cdimage
import native

SECTOR = 2048


def record(name, lba, size, directory=False):
    raw = name if isinstance(name, bytes) else (name + ('' if directory else ';1')).encode()
    length = 33 + len(raw) + (len(raw) + 1) % 2
    out = bytearray(length)
    out[0] = length
    struct.pack_into('<I', out, 2, lba); struct.pack_into('>I', out, 6, lba)
    struct.pack_into('<I', out, 10, size); struct.pack_into('>I', out, 14, size)
    out[25] = 2 if directory else 0
    out[32] = len(raw)
    out[33:33 + len(raw)] = raw
    return bytes(out)


def synthetic_iso(padding_sectors=0):
    """PVD at 16, root dir at 18, DATA dir at 19, files at 20 and 21."""
    files = {'SYSTEM.CNF': b'BOOT2 = cdrom0:\\TEST.ELF;1\n', 'DATA/LEVEL.BIN': bytes(range(256)) * 9}
    sectors = 23
    image = bytearray(sectors * SECTOR)
    pvd = bytearray(SECTOR)
    pvd[0] = 1; pvd[1:6] = b'CD001'; pvd[6] = 1
    struct.pack_into('<I', pvd, 80, sectors); struct.pack_into('>I', pvd, 84, sectors)
    struct.pack_into('<H', pvd, 128, SECTOR); struct.pack_into('>H', pvd, 130, SECTOR)
    pvd[156:190] = record(b'\x00', 18, SECTOR, True)
    image[16 * SECTOR:17 * SECTOR] = pvd
    image[17 * SECTOR] = 255; image[17 * SECTOR + 1:17 * SECTOR + 6] = b'CD001'
    root = record(b'\x00', 18, SECTOR, True) + record(b'\x01', 18, SECTOR, True)
    root += record('DATA', 19, SECTOR, True) + record('SYSTEM.CNF', 20, len(files['SYSTEM.CNF']))
    image[18 * SECTOR:18 * SECTOR + len(root)] = root
    data = record(b'\x00', 19, SECTOR, True) + record(b'\x01', 18, SECTOR, True)
    data += record('LEVEL.BIN', 21, len(files['DATA/LEVEL.BIN']))
    image[19 * SECTOR:19 * SECTOR + len(data)] = data
    image[20 * SECTOR:20 * SECTOR + len(files['SYSTEM.CNF'])] = files['SYSTEM.CNF']
    image[21 * SECTOR:21 * SECTOR + len(files['DATA/LEVEL.BIN'])] = files['DATA/LEVEL.BIN']
    return bytes(image) + bytes(padding_sectors * SECTOR), files


def manifest(files):
    return {'complete': True, 'expected_file_count': len(files), 'files': [
        {'path': path, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'status': 'copied'}
        for path, body in files.items()]}


class SectorImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.private = self.root / 'private'
        self.private.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, raw):
        path = self.root / 'source.iso'
        path.write_bytes(raw)
        return path

    def test_extents_walk_subdirectories(self):
        raw, _ = synthetic_iso()
        with self.source(raw).open('rb') as stream:
            sectors, files = cdimage.extents(stream)
        self.assertEqual(sectors, 23)
        self.assertEqual(files, {'SYSTEM.CNF': (20, 27), 'DATA/LEVEL.BIN': (21, 2304)})

    def test_raw_device_padding_is_trimmed_and_every_extent_verified(self):
        raw, files = synthetic_iso(padding_sectors=5)
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
        stage = cdimage.import_image(self.source(raw), self.private)
        image = self.private / cdimage.IMAGE
        self.assertTrue(stage['verified'] and stage['trimmed_from_source'])
        self.assertEqual(image.stat().st_size, 23 * SECTOR)
        self.assertEqual(cdimage.verified_image(self.private), image.resolve())

    def test_mismatched_extent_is_rejected_and_not_kept(self):
        raw, files = synthetic_iso()
        files['DATA/LEVEL.BIN'] = b'different'
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
        with self.assertRaisesRegex(ValueError, 'mismatched'):
            cdimage.import_image(self.source(raw), self.private)
        self.assertFalse((self.private / cdimage.IMAGE).exists())
        self.assertIsNone(cdimage.verified_image(self.private))

    def test_missing_file_and_short_or_foreign_sources_are_rejected(self):
        raw, files = synthetic_iso()
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(dict(files, EXTRA=b'x'))))
        with self.assertRaisesRegex(ValueError, 'missing'):
            cdimage.import_image(self.source(raw), self.private)
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
        with self.assertRaisesRegex(ValueError, 'shorter'):
            cdimage.import_image(self.source(raw[:-SECTOR]), self.private)
        with self.assertRaisesRegex(ValueError, 'primary volume descriptor'):
            cdimage.import_image(self.source(bytes(40 * SECTOR)), self.private)

    def test_requires_completed_file_import(self):
        raw, _ = synthetic_iso()
        with self.assertRaisesRegex(ValueError, 'Run import first'):
            cdimage.import_image(self.source(raw), self.private)

    def test_existing_private_image_is_verified_in_place(self):
        raw, files = synthetic_iso()
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
        (self.private / 'cd-image').mkdir()
        existing = self.private / 'cd-image/OWNED.iso'
        existing.write_bytes(raw)
        stage = cdimage.import_image(existing, self.private)
        self.assertEqual(stage['image'], 'cd-image/OWNED.iso')
        self.assertFalse((self.private / cdimage.IMAGE).exists())

    def test_changed_image_size_withdraws_acceptance(self):
        raw, files = synthetic_iso()
        (self.private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
        cdimage.import_image(self.source(raw), self.private)
        with (self.private / cdimage.IMAGE).open('ab') as f:
            f.write(b'x')
        self.assertIsNone(cdimage.verified_image(self.private))


class RuntimeReceivesImage(unittest.TestCase):
    def test_boot_probe_and_diagnose_pass_cd_image(self):
        raw, files = synthetic_iso()
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            (private / 'disc-manifest.json').write_text(json.dumps(manifest(files)))
            (private / 'cd-image').mkdir()
            (private / 'cd-image/disc.iso').write_bytes(raw)
            cdimage.import_image(private / 'cd-image/disc.iso', private)
            binary = private / 'runner'; binary.write_bytes(b'runner')
            (private / 'analysis/native-build-stage.json').write_text(json.dumps(
                {'binary': str(binary), 'sha256': hashlib.sha256(b'runner').hexdigest()}))
            (private / 'disc-root').mkdir()
            seen = []
            with mock.patch.object(native.lab, 'SOURCE', private / 'no-source'), \
                    mock.patch.object(native.lab, 'boot_executable', return_value=private / 'disc-root/TEST.ELF'), \
                    mock.patch.object(native, 'logged', side_effect=lambda *a, **k: seen.append(k['env_patch']) or 'timeout'):
                native.boot_probe({'id': 'synthetic'}, private)
                native.boot_probe({'id': 'synthetic'}, private, headless=True)
            for environment in seen:
                self.assertEqual(environment['PS2_PORT_CD_IMAGE'], str((private / 'cd-image/disc.iso').resolve()))
            self.assertEqual(len(seen), 2)

    def test_without_image_no_variable_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(native.cd_image(Path(tmp)))

    def test_debug_launcher_exports_configured_image(self):
        launcher = (Path(native.lab.ROOT) / 'cmake/LocalLauncher.m').read_text()
        self.assertIn('config[@"CdImage"]', launcher)
        self.assertIn('setenv("PS2_PORT_CD_IMAGE"', launcher)
        self.assertIn('unsetenv("PS2_PORT_CD_IMAGE")', launcher)
        self.assertIn("config['CdImage'] = str(image)", (Path(native.lab.ROOT) / 'native.py').read_text())


if __name__ == '__main__':
    unittest.main()
