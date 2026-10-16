"""Asset-free negative controls for the local memory gate."""
import struct
import unittest
import json
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import collisions, elf_regions, region, run_audit


class MemoryGate(unittest.TestCase):
    def test_game_owned_heap_detects_runtime_slot(self):
        self.assertEqual(collisions([region('packet', 0x9000, 64)],
            [region('guest heap', 0x8000, 0x8000)])[0]['overlap_bytes'], 64)

    def test_adjacent_regions_are_disjoint(self):
        self.assertEqual(collisions([region('packet', 0x4000, 0x1000)],
            [region('image', 0x5000, 0x2000)]), [])

    def test_runtime_reservations_must_not_overlap(self):
        self.assertEqual(len(collisions([region('a', 100, 64), region('b', 120, 64)], [])), 1)

    def test_physical_bounds(self):
        for start, size in [(0, 0), (-1, 4), (32*1024*1024-1, 2)]:
            with self.assertRaises(ValueError):
                region('invalid', start, size)

    def image(self):
        raw = bytearray(84)
        raw[:6] = b'\x7fELF\x01\x01'
        struct.pack_into('<H', raw, 18, 8)
        struct.pack_into('<I', raw, 28, 52)
        struct.pack_into('<HH', raw, 42, 32, 1)
        struct.pack_into('<8I', raw, 52, 1, 0, 0x80001000, 0, 0, 0x2000, 0, 0)
        return raw

    def test_bss_and_virtual_alias_overlap(self):
        loads = elf_regions(self.image())
        self.assertEqual(loads[0]['end'], 0x3000)
        self.assertTrue(collisions([region('host', 0x2000, 64)], loads))

    def test_truncated_program_table_rejected(self):
        with self.assertRaises(ValueError):
            elf_regions(self.image()[:-1])

    def fixture(self, folder):
        private = Path(folder)
        (private/'disc-root').mkdir()
        (private/'disc-root/TEST.ELF').write_bytes(self.image())
        header = private/'State.h'
        header.write_text('\n'.join(
            f'static constexpr uint32_t k{name}PoolBase = {0x10000+i*0x1000};\n'
            f'static constexpr uint32_t k{name}PoolBytes = 256;'
            for i, name in enumerate(['RpcPacket', 'RpcServer', 'Tls', 'BootMode'])))
        (private/'layout.json').write_text(json.dumps({'occupied':[
            {'name':'synthetic heap', 'start':'0x10000', 'size':'0x4000'}]}))
        return private, header, {'expected_executable':'TEST.ELF'}

    def test_pipeline_reports_collision_then_disjoint_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            private, header, profile = self.fixture(folder)
            report = run_audit(profile, private, header, 'layout.json', 'negative')
            self.assertFalse(report['accepted'])
            self.assertEqual(len(report['conflicts']), 4)
            (private/'layout.json').write_text(json.dumps({'occupied':[]}))
            self.assertTrue(run_audit(profile, private, header, 'layout.json', 'positive')['accepted'])
            with self.assertRaises(FileExistsError):
                run_audit(profile, private, header, 'layout.json', 'negative')
            saved = json.loads((private/'analysis/memory-audits/negative/report.json').read_text())
            self.assertEqual(saved['status'], 'collision')

    def test_pipeline_keeps_malformed_layout_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            private, header, profile = self.fixture(folder)
            (private/'layout.json').write_text('{}')
            with self.assertRaises(KeyError):
                run_audit(profile, private, header, 'layout.json', 'malformed')
            saved = json.loads((private/'analysis/memory-audits/malformed/report.json').read_text())
            self.assertFalse(saved['accepted'])
            self.assertIn('error', saved)

    def test_pipeline_rejects_layout_escape_and_output_symlink(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            private, header, profile = self.fixture(folder)
            (Path(outside)/'layout.json').write_text('{}')
            with self.assertRaises(ValueError):
                run_audit(profile, private, header, str(Path(outside)/'layout.json'), 'escape')
            (private/'analysis').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                run_audit(profile, private, header, 'layout.json', 'escape')

    def test_pipeline_rejects_wrong_disc_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            private, header, profile = self.fixture(folder)
            profile['expected_sha256'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                run_audit(profile, private, header, 'layout.json', 'wrong-disc')


if __name__ == '__main__':
    unittest.main()
