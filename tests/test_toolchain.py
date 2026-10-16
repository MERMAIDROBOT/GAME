import copy
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from toolchain import file_pin, verify_host


class HostToolchain(unittest.TestCase):
    def lock(self):
        return dict(schema=1, platform='Darwin', machine='arm64',
            selection={'sdk':'synthetic-sdk'}, versions={'clang':'synthetic-compiler'},
            files={'clang':{'path':'/synthetic/clang','sha256':'0'*64,'bytes':1}})

    def test_identical_host_matches(self):
        expected=self.lock()
        self.assertTrue(verify_host(expected,copy.deepcopy(expected))['matches'])

    def test_compiler_selection_sdk_version_and_binary_drift_rejected(self):
        expected=self.lock()
        for key in ['selection','versions','files','machine']:
            actual=copy.deepcopy(expected)
            actual[key]='changed'
            with self.subTest(key=key):
                self.assertEqual(verify_host(expected,actual)['differences'],[key])

    def test_missing_and_changed_binary_fingerprints(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic-tool'
            with self.assertRaises(ValueError):file_pin(path)
            path.write_bytes(b'first')
            first=file_pin(path)
            path.write_bytes(b'second')
            self.assertNotEqual(first['sha256'],file_pin(path)['sha256'])

    def test_wrong_lock_schema_rejected(self):
        with self.assertRaises(ValueError):verify_host({'schema':2}, {})


if __name__=='__main__':unittest.main()
