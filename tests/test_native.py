"""Bounded execution diagnostics use synthetic Python processes, never game data."""
from pathlib import Path
import sys
import json
import hashlib
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native


class NativeDiagnosticsTests(unittest.TestCase):
    def test_native_build_rejects_legacy_or_changed_knowledge_bank(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            (private/'analysis').mkdir()
            (private/'analysis/translation-stage.json').write_text(json.dumps({'success':True,'accepted':True}))
            (private/'generated').mkdir(); (private/'generated/a.cpp').write_text('void a() {}\n')
            with self.assertRaisesRegex(ValueError,'knowledge bank'):
                native.build_native({'id':'synthetic'},private)

    def test_native_build_requires_explicit_memory_ownership_gate(self):
        import knowledge
        from test_pipeline import minimal_elf
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            (private/'analysis').mkdir();(private/'disc-root').mkdir()
            (private/'disc-root/TEST.ELF').write_bytes(minimal_elf())
            bank=knowledge.load()
            expected=knowledge.sha(json.dumps(bank,sort_keys=True).encode())
            (private/'analysis/translation-stage.json').write_text(json.dumps({'knowledge_bank_sha256':expected,'accepted':True}))
            (private/'generated').mkdir(); (private/'generated/a.cpp').write_text('void a() {}\n')
            with self.assertRaisesRegex(ValueError,'ownership gate'):
                native.build_native({'id':'synthetic','expected_executable':'TEST.ELF'},private)

    def test_runtime_storage_verifies_repairs_and_preserves_newer_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / 'private'
            source = private / 'disc-root'
            source.mkdir(parents=True)
            (source / 'data.bin').write_bytes(b'synthetic disc bytes')
            (source / 'mc0').mkdir()
            (source / 'mc0/save').write_bytes(b'original save')
            record = {'path':'data.bin', 'bytes':20, 'sha256':hashlib.sha256(b'synthetic disc bytes').hexdigest(), 'status':'copied'}
            (private / 'disc-manifest.json').write_text(json.dumps({'complete':True,'files':[record]}))
            destination = Path(directory) / 'runtime'
            native.stage_runtime_disc(private, destination)
            self.assertEqual((destination / 'data.bin').read_bytes(), b'synthetic disc bytes')
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            (destination / 'mc0/save').write_bytes(b'newer runtime save')
            (destination / 'data.bin').write_bytes(b'tampered bytes')
            native.stage_runtime_disc(private, destination)
            self.assertEqual((destination / 'data.bin').read_bytes(), b'synthetic disc bytes')
            self.assertEqual((destination / 'mc0/save').read_bytes(), b'newer runtime save')
            self.assertEqual((source / 'mc0/save').read_bytes(), b'original save')
            record['path']='../outside'
            (private / 'disc-manifest.json').write_text(json.dumps({'files':[record]}))
            with self.assertRaises(ValueError):
                native.stage_runtime_disc(private, destination)
            record['path']='data.bin'
            (private / 'disc-manifest.json').write_text(json.dumps({'files':[record]}))
            (destination / 'data.bin').unlink()
            (destination / 'data.bin').symlink_to(source / 'data.bin')
            with self.assertRaises(ValueError):
                native.stage_runtime_disc(private, destination)

    def test_failure_is_recordable_and_log_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'probe.log'
            result = native.logged([sys.executable, '-c', 'print("synthetic failure", flush=True); raise SystemExit(7)'], log, allow_failure=True)
            self.assertEqual(result, 'exit_7')
            self.assertIn('synthetic failure', log.read_text())

    def test_timeout_stops_a_nonterminating_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'probe.log'
            result = native.logged([sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(10)'], log, timeout=0.2)
            self.assertEqual(result, 'timeout')
            self.assertIn('started', log.read_text())


if __name__ == '__main__':
    unittest.main()
