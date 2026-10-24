"""Asset-free checks for the IOP source patch and title-patch handling (Claude 2026-10-17)."""
import hashlib, re, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline as lab
import native

PATCH = ROOT / 'patches/ps2xIOP-port-lab.patch'


class IopPatchContent(unittest.TestCase):
    def setUp(self):
        self.text = PATCH.read_text()

    def test_order_and_whitelist(self):
        self.assertEqual(native.RUNTIME_PATCHES, ('patches/runtime-diagnostics.patch', 'patches/ps2xIOP-port-lab.patch', 'patches/scalar-fpu.patch', 'patches/runtime-title-conformance.patch', 'patches/scalar-fpu-compare.patch', 'patches/runtime-hotpaths.patch', 'patches/native-pcm.patch', 'patches/host-input.patch'))
        for name in native.RUNTIME_PATCHES:
            self.assertIn(name, lab.SOURCE_FILES)
        self.assertIn('tests/test_iop_patch.py', lab.SOURCE_FILES)

    def test_rpc_servers_honour_thread_priority(self):
        # The fix (servers yield to ready higher-priority threads) and its regression test ship together.
        self.assertIn('honourPriority', self.text)
        self.assertIn('kernel.beginNextReady(totalCycles)', self.text)
        self.assertIn('PS2_PORT_IOP_RPC_PRIORITY', self.text)
        self.assertIn('tests/rpc_priority_test.cpp', self.text)
        self.assertRegex(self.text, r'add_test\(NAME ps2_iop_rpc_priority_test')

    def test_every_iop_suite_is_registered_in_tree(self):
        tests = set(re.findall(r'add_executable\((ps2_iop_\w+) tests/', self.text))
        registered = set(re.findall(r'add_test\(NAME (ps2_iop_\w+)', self.text))
        self.assertTrue(tests and tests <= registered, tests - registered)
        self.assertNotRegex(self.text, r'add_executable\(\w+ \.\./')

    def test_public_patch_carries_no_title_code_or_private_paths(self):
        self.assertNotIn('/Users/', self.text)
        self.assertNotIn('GAS_UPDATE_LEAK', self.text)       # title patches stay in private profiles
        self.assertNotIn('applyTitleModulePatches', self.text)
        self.assertNotIn('0x8e040000u', self.text)            # no retail instruction signatures


class PrivateSourcePatches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.private = Path(self.tmp.name)
        self.patch = self.private / 'title.patch'; self.patch.write_text('x')

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, **kw):
        base = {'file': 'title.patch', 'sha256': hashlib.sha256(b'x').hexdigest(), 'reason': 'driver skip path vs real timer cadence'}
        base.update(kw); return {'private_source_patches': [base]}

    def test_requires_reason_and_hash(self):
        with self.assertRaises(ValueError):
            native.private_source_patches(self.item(reason=''), self.private)
        with self.assertRaises(ValueError):
            native.private_source_patches(self.item(sha256='0' * 64), self.private)

    def test_rejects_escapes(self):
        for bad in ('../title.patch', '/etc/hosts'):
            with self.assertRaises(ValueError):
                native.private_source_patches(self.item(file=bad), self.private)
        outside = tempfile.NamedTemporaryFile(delete=False); outside.write(b'x'); outside.close()
        (self.private / 'link.patch').symlink_to(outside.name)
        with self.assertRaises(ValueError):
            native.private_source_patches(self.item(file='link.patch'), self.private)

    def test_no_profile_entry_means_no_patches(self):
        self.assertEqual(native.private_source_patches({}, self.private), [])


class BuildScopedTitlePatch(unittest.TestCase):
    """A title patch is applied for one build and reversed afterwards, even on failure."""
    def test_reversed_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, private = Path(tmp) / 'src', Path(tmp) / 'private'
            src.mkdir(); private.mkdir()
            run = lambda *a: subprocess.run(['git', '-C', str(src), *a], check=True, capture_output=True)
            run('init', '-q'); (src / 'f.txt').write_text('public\n')
            run('add', '.'); run('-c', 'user.name=t', '-c', 'user.email=t@l', 'commit', '-q', '-m', 'p')
            (src / 'f.txt').write_text('title\n')
            diff = subprocess.run(['git', '-C', str(src), 'diff'], capture_output=True, text=True, check=True).stdout
            run('checkout', '-q', '--', '.')
            (private / 't.patch').write_text(diff)
            profile = {'private_source_patches': [{'file': 't.patch', 'reason': 'test',
                       'sha256': hashlib.sha256(diff.encode()).hexdigest()}]}
            with mock.patch.object(lab, 'SOURCE', src):
                with self.assertRaises(RuntimeError):
                    with native.private_patches_applied(profile, private):
                        self.assertEqual((src / 'f.txt').read_text(), 'title\n')
                        raise RuntimeError('build failed')
            self.assertEqual((src / 'f.txt').read_text(), 'public\n')


if __name__ == '__main__':
    unittest.main()
