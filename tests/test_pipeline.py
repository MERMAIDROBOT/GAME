"""Tests use synthetic inputs only; no retail data or network."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline


def minimal_elf(entry=0x100008):
    return b'\x7fELF\x01\x01\x01' + bytes(9) + struct.pack(
        '<HHIIIIIHHHHHH', 2, 8, 1, entry, 0, 0, 0, 52, 0, 0, 40, 0, 0)


class PipelineTests(unittest.TestCase):
    def test_r5900_sqrt_uses_ft_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.cpp'
            # Ft=12, Fs=0, Fd=1: sqrt of a squared vector length.
            path.write_text('// 0x1000: 0x460c0044  c1\n'
                            'ctx->pc = 0x1000u;\n'
                            'ctx->f[1] = FPU_SQRT_S(ctx->f[0]);\n')
            result = pipeline.correct_generated_sqrt(folder)
            self.assertEqual(result, {'instructions': 1, 'corrected': 1, 'files_changed': 1})
            self.assertIn('FPU_SQRT_S(ctx->f[12])', path.read_text())
            stamp = path.stat().st_mtime_ns
            self.assertEqual(pipeline.correct_generated_sqrt(folder)['corrected'], 0)
            self.assertEqual(stamp, path.stat().st_mtime_ns)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'disc'
        self.source.mkdir()
        self.private = self.base / 'private'
        (self.source / 'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\nVER = 1.00\nVMODE = NTSC')
        (self.source / 'TEST.ELF').write_bytes(minimal_elf())
        (self.source / 'asset.dat').write_bytes(bytes(range(256)))
        self.profile = {'id': 'synthetic', 'title': 'Synthetic test'}

    def tearDown(self):
        self.temp.cleanup()

    def test_source_package_excludes_unlisted_private_files(self):
        root = self.base / 'source'
        for name in pipeline.SOURCE_FILES:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic public source')
        (root / 'private-game.bin').write_bytes(b'synthetic excluded data')
        with contextlib.redirect_stdout(io.StringIO()):
            archive = pipeline.package_source(root)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.namelist(), ['ps2-port-lab/' + n for n in pipeline.SOURCE_FILES])
            self.assertIsNone(z.testzip())

    def test_source_package_rejects_whitelisted_symlinks(self):
        root = self.base / 'source'
        for name in pipeline.SOURCE_FILES:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic public source')
        (root / 'native.py').unlink()
        (root / 'native.py').symlink_to(self.source / 'asset.dat')
        with self.assertRaisesRegex(ValueError, 'unsafe source package member'):
            pipeline.package_source(root)

    def test_import_verify_and_resume_without_rereading_assets(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.import_disc(self.source, self.profile, self.private)
            pipeline.verify(self.profile, self.private)
        original = Path.open

        def guard(path, *args, **kwargs):
            if path == self.source / 'asset.dat':
                raise AssertionError('Resumed import unnecessarily reread asset')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', guard), contextlib.redirect_stdout(io.StringIO()):
            pipeline.import_disc(self.source, self.profile, self.private)
        m = json.loads((self.private / 'disc-manifest.json').read_text())
        self.assertTrue(m['complete'])
        self.assertEqual(len(m['files']), 3)

    def test_tampering_is_detected(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.import_disc(self.source, self.profile, self.private)
        (self.private / 'disc-root/asset.dat').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'Verification failed'):
            pipeline.verify(self.profile, self.private)

    def test_other_executable_cannot_silently_reuse_previous_input(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.import_disc(self.source, self.profile, self.private)
        (self.source / 'TEST.ELF').write_bytes(minimal_elf(entry=0x200008))
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            pipeline.import_disc(self.source, self.profile, self.private)

    def test_import_failure_retains_completed_files(self):
        original = Path.open

        def fail_asset(path, *args, **kwargs):
            if path == self.source / 'asset.dat':
                raise OSError('simulated reader dropout')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', fail_asset), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                pipeline.import_disc(self.source, self.profile, self.private)
        m = json.loads((self.private / 'disc-manifest.json').read_text())
        self.assertFalse(m['complete'])
        self.assertEqual(len(m['files']), 2)
        self.assertTrue((self.private / 'disc-root/TEST.ELF').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.import_disc(self.source, self.profile, self.private)
            pipeline.verify(self.profile, self.private)

    def test_traversal_and_symlinks_are_rejected(self):
        for bad in ['../escape', '/absolute', 'dir/../../escape', 'dir\\escape']:
            with self.assertRaises(ValueError):
                pipeline.safe_relative(bad)
        (self.source / 'link').symlink_to(self.source / 'TEST.ELF')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            pipeline.import_disc(self.source, self.profile, self.private)

    def test_malformed_elf_is_rejected(self):
        self.assertEqual(pipeline.elf_info(self.source / 'TEST.ELF')['entry'], '0x100008')
        broken = bytearray(minimal_elf())
        struct.pack_into('<I', broken, 32, 10000)  # Section table beyond file.
        struct.pack_into('<H', broken, 48, 2)
        (self.source / 'TEST.ELF').write_bytes(broken)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            pipeline.elf_info(self.source / 'TEST.ELF')


if __name__ == '__main__':
    unittest.main()
