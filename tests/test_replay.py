import fcntl
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import rawgs
import replay
from pipeline import digest


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / 'title-a'
        self.private.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        patcher = patch.object(replay, 'WORK', self.work)
        patcher.start()
        self.addCleanup(patcher.stop)
        data = b'PGSCAP2\0' + struct.pack('<II', 2, 1)
        for n, (op, payload) in enumerate([(1, bytes(rawgs.VRAM_SIZE)), (6, bytes(rawgs.VRAM_SIZE))], 1):
            data += struct.pack('<IIQ', op, len(payload), n) + payload
        (self.private / 'capture').write_bytes(data)
        self.loader = self.root / 'loader'
        self.loader.write_bytes(b'synthetic loader')
        self.backend()

    def backend(self, mode='valid'):
        # Separate process exercises the actual argv/output/exit/timeout contract.
        exe = self.root / 'synthetic-backend'
        exe.write_text('''#!/usr/bin/env python3
import json, pathlib, sys, time, os
out=pathlib.Path(sys.argv[sys.argv.index('--output')+1])
mode=%r
if mode=='exit': sys.exit(9)
if mode=='timeout': time.sleep(10)
if mode=='missing': sys.exit(0)
snapshot=bytes([1 if mode in ('mismatch','contradiction') else 0])*4194304
(out/'vram.bin').write_bytes(snapshot)
result={'stage':'complete','status':'passed','records_replayed':2,'compared_vram_bytes':4194304,'mismatched_bytes':0,
        'batch_observed':os.environ['PS2_PORT_PRIVATE_GIF_BATCH']}
if mode=='mismatch': result.update(status='pixel_mismatch',mismatched_bytes=4194304)
if mode=='coverage': result['records_replayed']=1
(out/'result.json').write_text(json.dumps(result))
''' % mode)
        exe.chmod(0o755)
        spec = {'schema': 1, 'protocol': 'pgscap-cli-v1',
                'executable': {'path': str(exe), 'sha256': digest(exe)},
                'moltenvk': {'path': str(self.loader), 'sha256': digest(self.loader)}}
        (self.private / 'backend.json').write_text(json.dumps(spec))

    def run_probe(self, name='run', **kwargs):
        return replay.run_replay({'id': self.private.name}, self.private, 'capture', 'backend.json', name, **kwargs)

    def test_two_profiles_and_treatment(self):
        first = self.run_probe(batch='off')
        self.assertEqual(first['status'], 'observed')
        self.assertTrue(first['matches_recorded_footer'])
        self.assertEqual(first['native_result']['batch_observed'], '0')
        self.assertFalse(first['gameplay_verified'])
        other = self.root / 'title-b'
        other.mkdir()
        for name in ('capture', 'backend.json'):
            (other / name).write_bytes((self.private / name).read_bytes())
        self.private = other
        second = self.run_probe()
        self.assertEqual(second['profile_id'], 'title-b')
        self.assertEqual(second['native_result']['batch_observed'], '1')

    def test_mismatch_is_observed_not_rendering_acceptance(self):
        self.backend('mismatch')
        result = self.run_probe()
        self.assertEqual(result['status'], 'observed')
        self.assertFalse(result['matches_recorded_footer'])
        self.assertFalse(result['original_fidelity_verified'])

    def test_incomplete_contradictory_or_failed_results_rejected(self):
        for mode in ('exit', 'missing', 'coverage', 'contradiction'):
            with self.subTest(mode=mode):
                self.backend(mode)
                result = self.run_probe(name=mode)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(json.loads((self.private / 'logs/replays' / mode / 'report.json').read_text())['status'], 'failed')

    def test_timeout_is_recorded(self):
        self.backend('timeout')
        self.assertEqual(self.run_probe(timeout=1)['status'], 'failed')
        # Timeout releases the lock after the owned subprocess is reaped.
        self.backend()
        self.assertEqual(self.run_probe(name='after')['status'], 'observed')

    def test_changed_backend_rejected_before_launch(self):
        self.loader.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.run_probe()
        self.assertFalse((self.private / 'logs').exists())

    def test_escape_incomplete_and_duplicate_rejected(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'x')
        (self.private / 'escape').symlink_to(outside)
        with self.assertRaises(ValueError):
            replay.run_replay({'id': 'a'}, self.private, 'escape', 'backend.json', 'run')
        self.run_probe()
        with self.assertRaises(FileExistsError):
            self.run_probe()
        (self.private / 'capture').write_bytes(b'PGSCAP2\0' + struct.pack('<II',2,1))
        with self.assertRaises(ValueError):
            self.run_probe(name='bad')

    def test_overlapping_replay_rejected(self):
        with (self.work / '.raw-gs-replay.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another pipeline'):
                self.run_probe()
        self.assertFalse((self.private / 'logs').exists())
