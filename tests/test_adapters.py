import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters import guarded_bytes,stage_adapters


class TitleAdapters(unittest.TestCase):
    def test_guards_and_copy_preserve_original(self):
        raw=b'abcdefgh'
        self.assertEqual(guarded_bytes(raw,[{'offset':0,'before':'6162'}],
            [{'offset':2,'before':'6364','after':'3132'}]),b'ab12efgh')
        self.assertEqual(raw,b'abcdefgh')

    def test_overlap_bounds_wrong_bytes_and_size_changes_fail(self):
        cases=[
            [{'offset':0,'before':'61','after':'3132'}],
            [{'offset':-1,'before':'61','after':'31'}],
            [{'offset':0,'before':'ff','after':'31'}],
            [{'offset':0,'before':'6162','after':'3132'}, {'offset':1,'before':'62','after':'33'}]]
        for edits in cases:
            with self.subTest(edits=edits),self.assertRaises(ValueError):
                guarded_bytes(b'abcdefgh',[],edits)

    def fixture(self,folder):
        root=Path(folder)
        (root/'disc-root').mkdir()
        (root/'disc-root/TEST.ELF').write_bytes(b'synthetic ELF identity')
        (root/'disc-root/MODULE.IRX').write_bytes(b'abcdefgh')
        identity=hashlib.sha256(b'synthetic ELF identity').hexdigest()
        profile=dict(id='synthetic',expected_executable='TEST.ELF',expected_sha256=identity)
        spec=dict(schema=1,title_id='synthetic',executable_sha256=identity,adapters=[dict(
            id='synthetic-repair',file='MODULE.IRX',input_sha256=hashlib.sha256(b'abcdefgh').hexdigest(),
            reason='synthetic test',removal_condition='upstream repair accepted',
            guards=[{'offset':0,'before':'6162'}],edits=[{'offset':2,'before':'6364','after':'3132'}])])
        return root,profile,spec

    def test_staging_keeps_disc_and_rejects_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder)
            (root/'adapters.json').write_text(json.dumps(spec))
            report=stage_adapters(profile,root,'adapters.json','first')
            self.assertEqual(report['status'],'staged')
            self.assertEqual((root/'disc-root/MODULE.IRX').read_bytes(),b'abcdefgh')
            self.assertEqual((root/'adapter-runs/first/files/MODULE.IRX').read_bytes(),b'ab12efgh')
            with self.assertRaises(FileExistsError):stage_adapters(profile,root,'adapters.json','first')

    def test_second_file_failure_emits_no_patched_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder)
            (root/'disc-root/SECOND.IRX').write_bytes(b'abcdefgh')
            second=dict(spec['adapters'][0],id='second',file='SECOND.IRX',input_sha256='0'*64)
            spec['adapters'].append(second)
            (root/'adapters.json').write_text(json.dumps(spec))
            with self.assertRaises(ValueError):stage_adapters(profile,root,'adapters.json','bad')
            self.assertFalse((root/'adapter-runs/bad/files').exists())
            report=json.loads((root/'adapter-runs/bad/report.json').read_text())
            self.assertEqual(report['status'],'failed')

    def test_wrong_disc_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder)
            spec['executable_sha256']='0'*64
            (root/'adapters.json').write_text(json.dumps(spec))
            with self.assertRaises(ValueError):stage_adapters(profile,root,'adapters.json','wrong')
            spec['executable_sha256']=profile['expected_sha256']
            spec['adapters'][0]['file']='../outside'
            (root/'adapters.json').write_text(json.dumps(spec))
            with self.assertRaises(ValueError):stage_adapters(profile,root,'adapters.json','escape')


if __name__=='__main__':unittest.main()
