"""Guards against module-level ordering mistakes that tests of behaviour miss."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModuleLayoutTests(unittest.TestCase):
    def test_pipeline_main_guard_is_last(self):
        # `python pipeline.py source-package` runs cli() at the guard; any
        # SOURCE_FILES additions after it would be silently left out.
        body = ast.parse((ROOT / 'pipeline.py').read_text()).body
        last = body[-1]
        self.assertIsInstance(last, ast.If)
        self.assertIn('__main__', ast.unparse(last.test))

    def test_package_local_holds_exclusive_slot(self):
        tree = ast.parse((ROOT / 'native.py').read_text())
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        decorators = [ast.unparse(d) for d in functions['package_local'].decorator_list]
        self.assertIn("exclusive('local-app-package')", decorators)
        self.assertEqual(functions['copy_bundle_file'].decorator_list, [])
        self.assertIsNotNone(ast.get_docstring(functions['package_local']))


if __name__ == '__main__':
    unittest.main()
