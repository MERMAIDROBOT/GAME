"""The shared checkout follows public patches that are edited in place (folded fixes)."""
from pathlib import Path
from unittest import mock
import shutil
import subprocess
import tempfile
import unittest
import native
import pipeline as lab


def diff(old, new, name='f.txt'):
    """A unified diff with git a/ b/ prefixes for one file."""
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / 'a', Path(tmp) / 'b'
        a.write_text(old); b.write_text(new)
        out = subprocess.run(['git', 'diff', '--no-index', '--src-prefix=a/', '--dst-prefix=b/', str(a), str(b)],
                             capture_output=True, text=True).stdout
    return out.replace(f'a{a}', f'a/{name}').replace(f'b{b}', f'b/{name}')


@unittest.skipUnless(shutil.which('git'), 'git unavailable')
class RuntimePatchStack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.source, self.lab = root / 'src', root / 'lab'
        self.source.mkdir(); (self.lab / 'patches').mkdir(parents=True)
        base = ''.join(f'line {i}\n' for i in range(20))
        (self.source / 'f.txt').write_text(base)
        for cmd in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
            subprocess.run(['git', '-C', str(self.source), *cmd], check=True)
        self.base = base
        one = base.replace('line 2\n', 'line 2 first\n')
        (self.lab / 'patches/one.patch').write_text(diff(base, one))
        (self.lab / 'patches/two.patch').write_text(diff(one, one.replace('line 10\n', 'line 10 v1\n')))
        self.one = one
        self.patches = ('patches/one.patch', 'patches/two.patch')

    def tearDown(self): self.tmp.cleanup()

    def apply(self):
        with mock.patch.object(lab, 'SOURCE', self.source), mock.patch.object(lab, 'ROOT', self.lab), \
             mock.patch.object(native, 'RUNTIME_PATCHES', self.patches):
            native.apply_runtime_patch()
        return (self.source / 'f.txt').read_text()

    def test_patch_edited_in_place_is_peeled_from_the_recorded_copy(self):
        self.assertIn('line 10 v1', self.apply())
        self.assertEqual(len(list((self.source / '.git/port-lab-applied-stack').glob('*.patch'))), 2)
        self.assertIn('line 10 v1', self.apply())  # idempotent
        # Fold a new fix into the last patch: its new content no longer reverses from the tree.
        (self.lab / 'patches/two.patch').write_text(diff(self.one, self.one.replace('line 10\n', 'line 10 v2\n')
                                                                          .replace('line 15\n', 'line 15 v2\n')))
        text = self.apply()
        self.assertIn('line 10 v2', text); self.assertIn('line 15 v2', text); self.assertIn('line 2 first', text)
        self.assertNotIn('v1', text)


if __name__ == '__main__':
    unittest.main()
