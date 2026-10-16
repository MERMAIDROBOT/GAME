import sys
from pathlib import Path
import unittest
import hashlib
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dependencies import dependency_paths, verify_files


class DependencyFiles(unittest.TestCase):
    def test_continuations_and_duplicate_headers(self):
        paths = dependency_paths('object: source.cpp \\\n include/a.h source.cpp\n', '/tmp/project')
        self.assertEqual(paths, [Path('/tmp/project/source.cpp').resolve(), Path('/tmp/project/include/a.h').resolve()])

    def test_filename_spaces_hash_dollar_quote_colon(self):
        paths = dependency_paths("obj: a\\ b.h hash\\#.h dollar$$.h quote'.h colon\\:.h", '/tmp')
        self.assertEqual([p.name for p in paths], ['a b.h', 'hash#.h', 'dollar$.h', "quote'.h", 'colon:.h'])

    def test_absolute_and_relative_paths(self):
        self.assertEqual(dependency_paths('obj: /tmp/a.h b.h', '/tmp'), [Path('/tmp/a.h').resolve(),Path('/tmp/b.h').resolve()])

    def test_rejects_expressions_multiple_rules_and_truncation(self):
        for value in ['obj: $(shell bad)', 'obj: ${ROOT}/a.h', 'obj: a.h\nother: b.h',
                      'obj: a.h\\', 'obj: a.h #comment', 'no separator', 'obj:']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                dependency_paths(value, '/tmp')

    def test_changed_and_missing_dependency_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'header.h'
            path.write_bytes(b'first')
            records = [dict(path=str(path), sha256=hashlib.sha256(b'first').hexdigest())]
            self.assertEqual(verify_files(records), [])
            path.write_bytes(b'second')
            self.assertEqual(verify_files(records)[0]['reason'], 'changed')
            records[0]['path'] = str(Path(folder)/'missing.h')
            self.assertEqual(verify_files(records)[0]['reason'], 'missing')


if __name__ == '__main__':
    unittest.main()
