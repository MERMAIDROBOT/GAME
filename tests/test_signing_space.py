"""Packaging storage preflight uses metadata only and preserves existing files."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from native import require_signing_space

class SigningSpace(unittest.TestCase):
    def test_low_space_fails_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'runner';binary.write_bytes(b'preserved')
            app=root/'future'/'preview.app'
            with patch('native.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(RuntimeError,'Insufficient temporary space'):
                    require_signing_space(app,binary,{})
            self.assertFalse(app.parent.exists())
            self.assertEqual(binary.read_bytes(),b'preserved')
    def test_budget_includes_frameworks_and_signer_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'runner';binary.write_bytes(b'a'*100)
            framework=root/'framework';framework.write_bytes(b'b'*70)
            with patch('native.shutil.disk_usage',return_value=SimpleNamespace(free=1<<30)):
                report=require_signing_space(root,binary,{'x':framework})
            self.assertEqual(report['required_bytes'],(32<<20)+340)

    def test_packaging_runs_preflight_before_creating_any_bundle(self):
        import hashlib,json,native
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private=root/'private';(private/'analysis').mkdir(parents=True)
            binary=root/'runner';binary.write_bytes(b'unchanged compiled runner')
            stage=dict(binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),overlays=None)
            (private/'analysis/native-build-stage.json').write_text(json.dumps(stage))
            engine=root/'engine';engine.mkdir()
            with patch('native.assert_runtime_slot_idle'), patch('native.overlay_build_receipt',return_value=None), \
                 patch('generated_corrections.verify'), patch('native_graph.framework_inputs',return_value={}), \
                 patch.object(native.lab,'ROOT',engine), \
                 patch('native.require_signing_space',side_effect=RuntimeError('preflight sentinel')) as preflight:
                with self.assertRaisesRegex(RuntimeError,'preflight sentinel'):
                    native.package_local(dict(title='Synthetic signing test',id='synthetic'),private)
            preflight.assert_called_once_with(root,binary,{})
            self.assertFalse((root/'Synthetic signing test (Debug).app').exists())
            self.assertEqual(binary.read_bytes(),b'unchanged compiled runner')
            external=root/'external'
            with patch('native.assert_runtime_slot_idle'), patch('native.overlay_build_receipt',return_value=None), \
                 patch('generated_corrections.verify'), patch('native_graph.framework_inputs',return_value={}), \
                 patch('native.require_signing_space',side_effect=RuntimeError('external preflight')) as preflight:
                with self.assertRaisesRegex(RuntimeError,'external preflight'):
                    native.package_local(dict(title='External test',id='external'),private,output_directory=external)
            preflight.assert_called_once_with(external,binary,{})
            self.assertFalse(external.exists())
            with patch('native.assert_runtime_slot_idle'), patch('native.overlay_build_receipt',return_value=None), \
                 patch('generated_corrections.verify'), patch('native_graph.framework_inputs',return_value={}):
                with self.assertRaisesRegex(ValueError,'absolute path'):
                    native.package_local(dict(title='Bad output',id='bad'),private,output_directory='relative')


class NativeLinkSpace(unittest.TestCase):
    def test_previous_runner_budgets_replacement_and_scratch_without_mutation(self):
        from native import require_native_link_space
        with tempfile.TemporaryDirectory() as tmp:
            build=Path(tmp);binary=build/'ps2xRuntime/ps2EntryRunner'
            binary.parent.mkdir();binary.write_bytes(b'previous runner')
            with patch('native.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(RuntimeError,'native relink'):
                    require_native_link_space(build)
            self.assertEqual(binary.read_bytes(),b'previous runner')
            with patch('native.shutil.disk_usage',return_value=SimpleNamespace(free=1<<30)):
                report=require_native_link_space(build)
            self.assertEqual(report['required_bytes'],3*len(b'previous runner')+(64<<20))
    def test_first_build_does_not_invent_a_previous_runner_size(self):
        from native import require_native_link_space
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(require_native_link_space(Path(tmp))['estimated'])
