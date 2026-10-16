import json
from pathlib import Path
import tempfile
import unittest
from platform_adapter import inspect_adapter, run_adapter


class PlatformAdapters(unittest.TestCase):
    def fixture(self, root):
        (root/'entry.py').write_text('import os,sys\nassert os.path.isfile(os.path.join(os.environ["PS2_PORT_LAB_CORE"],"pipeline.py"))\nsys.exit(7)\n')
        path=root/'adapter.json'
        path.write_text(json.dumps(dict(schema=1,core_api=1,id='synthetic',systems=['Linux'],entrypoint='entry.py')))
        return path

    def test_explicit_dispatch_preserves_exit_and_host_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.fixture(Path(tmp))
            self.assertEqual(run_adapter(p,[],system='Linux'),7)
            with self.assertRaisesRegex(ValueError,'host'): run_adapter(p,[],system='Darwin')

    def test_discovery_does_not_execute_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.fixture(Path(tmp));self.assertEqual(inspect_adapter(p)[0]['id'],'synthetic')
            d=json.loads(p.read_text());d['entrypoint']='../outside.py';p.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,'escapes'): inspect_adapter(p)

    def test_unknown_api_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.fixture(root);d=json.loads(p.read_text());d['core_api']=2;p.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,'API'): inspect_adapter(p)
            d['core_api']=1;d['entrypoint']='link.py';p.write_text(json.dumps(d));(root/'link.py').symlink_to(root/'entry.py')
            with self.assertRaisesRegex(ValueError,'escapes'): inspect_adapter(p)
