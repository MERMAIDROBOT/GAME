"""Synthetic compiler provenance/staging controls; no retail data or network."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline
import compiler


class CompilerRecipes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.game = self.root/'game'
        (self.game/'disc-root').mkdir(parents=True)
        (self.game/'disc-root/TEST.ELF').write_bytes(b'synthetic boot identity')
        self.source = self.game/'runtime.cpp'
        self.source.write_text('int synthetic_runtime() { return 7; }\n')
        self.profile = dict(id='synthetic', expected_executable='TEST.ELF')
        self.recipe = dict(schema=1, id='synthetic-compiler', title_id='synthetic',
            executable_sha256=pipeline.digest(self.game/'disc-root/TEST.ELF'),
            toolchain_lock_sha256=pipeline.digest(pipeline.ROOT/'toolchain.lock.json'),
            components=[dict(id='runtime', scope='shared', qualification='synthetic',
                clean_source=True, files=[dict(root='game', path='runtime.cpp',
                    role='source', sha256=pipeline.digest(self.source))])])
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        (self.game/'recipe.json').write_text(json.dumps(self.recipe))

    def inspect(self):
        return compiler.inspect_recipe(self.profile, self.game, 'recipe.json')

    def test_complete_recipe_stages_owned_copy_without_overwriting(self):
        result = compiler.run_recipe(self.profile, self.game, 'recipe.json', 'first', stage=True)
        self.assertTrue(result['clean_build_ready'])
        self.source.write_text('changed later')
        frozen = self.game/'compiler-runs/first/sources/runtime/game/runtime.cpp'
        self.assertIn('return 7', frozen.read_text())
        self.assertEqual(result['status'], 'staged')
        self.source.write_text(frozen.read_text())
        with self.assertRaises(FileExistsError):
            compiler.run_recipe(self.profile, self.game, 'recipe.json', 'first', stage=True)

    def test_cached_object_is_not_clean_source(self):
        c = self.recipe['components'][0]
        c.update(clean_source=False, qualification='reported')
        c['files'][0]['role'] = 'object'
        self.write()
        result = compiler.run_recipe(self.profile, self.game, 'recipe.json', 'cache', stage=True)
        self.assertFalse(result['clean_build_ready'])
        self.assertEqual(len(result['blockers']), 3)
        self.assertEqual(result['staged_files'], [])

    def test_changed_source_toolchain_and_disc_are_rejected(self):
        for key in ['source', 'toolchain', 'disc']:
            with self.subTest(key=key):
                original = json.loads(json.dumps(self.recipe))
                if key == 'source':
                    self.recipe['components'][0]['files'][0]['sha256'] = '0'*64
                elif key == 'toolchain':
                    self.recipe['toolchain_lock_sha256'] = '0'*64
                else:
                    self.recipe['executable_sha256'] = '0'*64
                self.write()
                with self.assertRaises(ValueError):
                    self.inspect()
                self.recipe = original

    def test_escaping_symlink_and_traversal_inputs_are_rejected(self):
        item = self.recipe['components'][0]['files'][0]
        (self.game/'external.cpp').symlink_to(self.root/'outside.cpp')
        (self.root/'outside.cpp').write_text('outside')
        for path in ['../outside.cpp', 'external.cpp']:
            item['path'] = path
            self.write()
            with self.assertRaises(ValueError):
                self.inspect()

    def test_unknown_or_duplicate_components_rejected(self):
        self.recipe['components'].append(self.recipe['components'][0].copy())
        self.write()
        with self.assertRaises(ValueError):
            self.inspect()
        self.recipe['components'].pop()
        self.recipe['components'][0]['scope'] = 'magic'
        self.write()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_copy_race_is_rejected_and_failed_report_retained(self):
        def corrupt_copy(source, destination):
            Path(destination).write_text('wrong content')
        with patch.object(compiler.shutil, 'copyfile', side_effect=corrupt_copy):
            with self.assertRaises(ValueError):
                compiler.run_recipe(self.profile, self.game, 'recipe.json', 'race', stage=True)
        report = json.loads((self.game/'compiler-runs/race/report.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['clean_build_ready'])

    def test_output_escape_rejected(self):
        (self.game/'compiler-runs').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            compiler.run_recipe(self.profile, self.game, 'recipe.json', 'escape', stage=True)

    def closure(self):
        sdk = self.root/'sdk.h'
        sdk.write_text('synthetic SDK')
        data = dict(status='passed', units=[dict(text_matches=True,
            reached_original_project_files=[], inputs=[
                dict(kind='project', original=str(self.source), staged=str(self.source),
                     sha256=pipeline.digest(self.source)),
                dict(kind='toolchain', original=str(sdk), staged=None,
                     sha256=pipeline.digest(sdk))])])
        path = self.game/'closure.json'
        path.write_text(json.dumps(data))
        self.recipe['components'][0]['dependency_closure'] = dict(
            root='game',path='closure.json',role='evidence',sha256=pipeline.digest(path))
        self.write()
        return sdk, path, data

    def test_dependency_closure_checks_external_sdk_pins(self):
        sdk, _, _ = self.closure()
        report = self.inspect()
        self.assertEqual(report['components'][0]['verified_dependency_files'], 2)
        sdk.write_text('updated SDK')
        with self.assertRaisesRegex(ValueError, 'SDK dependency changed'):
            self.inspect()

    def test_dependency_closure_rejects_unisolated_or_failed_evidence(self):
        _, path, data = self.closure()
        data['units'][0]['reached_original_project_files'] = ['old-header.h']
        path.write_text(json.dumps(data))
        self.recipe['components'][0]['dependency_closure']['sha256'] = pipeline.digest(path)
        self.write()
        with self.assertRaisesRegex(ValueError, 'isolation'):
            self.inspect()

    def test_recipe_rejects_changed_host_toolchain_selection(self):
        expected = dict(schema=1, platform='Darwin', machine='synthetic',
                        selection={'sdk':'first'}, versions={}, files={})
        path = self.game/'host.json'
        path.write_text(json.dumps(expected))
        self.recipe['host_toolchain'] = dict(root='game', path='host.json',
            role='config', sha256=pipeline.digest(path))
        self.write()
        with patch('toolchain.capture_host', return_value=expected):
            self.assertTrue(self.inspect()['clean_build_ready'])
        with patch('toolchain.capture_host', return_value=dict(expected, selection={'sdk':'second'})):
            with self.assertRaisesRegex(ValueError, 'compiler/linker/SDK'):
                self.inspect()


if __name__ == '__main__':
    unittest.main()
