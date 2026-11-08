"""Bounded execution diagnostics use synthetic Python processes, never game data."""
from pathlib import Path
import sys
import json
import hashlib
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native


class NativeDiagnosticsTests(unittest.TestCase):
    def test_native_build_rejects_legacy_or_changed_knowledge_bank(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            (private/'analysis').mkdir()
            (private/'analysis/translation-stage.json').write_text(json.dumps({'success':True,'accepted':True}))
            (private/'generated').mkdir(); (private/'generated/a.cpp').write_text('void a() {}\n')
            with self.assertRaisesRegex(ValueError,'knowledge bank'):
                native.build_native({'id':'synthetic'},private)

    def test_native_build_requires_explicit_memory_ownership_gate(self):
        import knowledge
        from test_pipeline import minimal_elf
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            (private/'analysis').mkdir();(private/'disc-root').mkdir()
            (private/'disc-root/TEST.ELF').write_bytes(minimal_elf())
            bank=knowledge.load()
            expected=knowledge.sha(json.dumps(bank,sort_keys=True).encode())
            (private/'analysis/translation-stage.json').write_text(json.dumps({'knowledge_bank_sha256':expected,'accepted':True}))
            (private/'generated').mkdir(); (private/'generated/a.cpp').write_text('void a() {}\n')
            with self.assertRaisesRegex(ValueError,'ownership gate'):
                native.build_native({'id':'synthetic','expected_executable':'TEST.ELF'},private)

    def test_legacy_build_checks_compute_guard_before_source_mutation(self):
        from unittest.mock import patch
        import knowledge
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            stage={'knowledge_bank_sha256':knowledge.sha(json.dumps({},sort_keys=True).encode())}
            with patch.object(native.lab,'require_accepted_translation',return_value=stage), \
                 patch.object(native.lab,'generated_sources'),patch.object(knowledge,'load',return_value={}), \
                 patch.object(knowledge,'plan',return_value={'memory':{'accepted':True}}), \
                 patch.object(native,'assert_idle',side_effect=RuntimeError('slot occupied')), \
                 patch.object(native,'apply_runtime_patch') as mutation:
                with self.assertRaisesRegex(RuntimeError,'occupied'):native.build_native({},private)
                mutation.assert_not_called()

    def test_source_graph_branch_uses_its_own_builder_lock_without_nested_acquisition(self):
        from unittest.mock import patch
        import knowledge
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);(private/'analysis').mkdir()
            stage={'knowledge_bank_sha256':knowledge.sha(json.dumps({},sort_keys=True).encode())}
            with patch.object(native.lab,'require_accepted_translation',return_value=stage), \
                 patch.object(native.lab,'generated_sources'),patch.object(knowledge,'load',return_value={}), \
                 patch.object(knowledge,'plan',return_value={'memory':{'accepted':True}}), \
                 patch('native_graph.build',return_value={}) as build, \
                 patch.object(native,'slot',side_effect=AssertionError('nested lock')):
                native.build_native({'native_source_graph':{}},private)
                build.assert_called_once()

    def test_runtime_storage_verifies_repairs_and_preserves_newer_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / 'private'
            source = private / 'disc-root'
            source.mkdir(parents=True)
            (source / 'data.bin').write_bytes(b'synthetic disc bytes')
            (source / 'mc0').mkdir()
            (source / 'mc0/save').write_bytes(b'original save')
            record = {'path':'data.bin', 'bytes':20, 'sha256':hashlib.sha256(b'synthetic disc bytes').hexdigest(), 'status':'copied'}
            (private / 'disc-manifest.json').write_text(json.dumps({'complete':True,'files':[record]}))
            destination = Path(directory) / 'runtime'
            native.stage_runtime_disc(private, destination)
            self.assertEqual((destination / 'data.bin').read_bytes(), b'synthetic disc bytes')
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            (destination / 'mc0/save').write_bytes(b'newer runtime save')
            (destination / 'data.bin').write_bytes(b'tampered bytes')
            native.stage_runtime_disc(private, destination)
            self.assertEqual((destination / 'data.bin').read_bytes(), b'synthetic disc bytes')
            self.assertEqual((destination / 'mc0/save').read_bytes(), b'newer runtime save')
            self.assertEqual((source / 'mc0/save').read_bytes(), b'original save')
            record['path']='../outside'
            (private / 'disc-manifest.json').write_text(json.dumps({'files':[record]}))
            with self.assertRaises(ValueError):
                native.stage_runtime_disc(private, destination)
            record['path']='data.bin'
            (private / 'disc-manifest.json').write_text(json.dumps({'files':[record]}))
            (destination / 'data.bin').unlink()
            (destination / 'data.bin').symlink_to(source / 'data.bin')
            with self.assertRaises(ValueError):
                native.stage_runtime_disc(private, destination)

    def test_failure_is_recordable_and_log_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'probe.log'
            result = native.logged([sys.executable, '-c', 'print("synthetic failure", flush=True); raise SystemExit(7)'], log, allow_failure=True)
            self.assertEqual(result, 'exit_7')
            self.assertIn('synthetic failure', log.read_text())

    def test_timeout_stops_a_nonterminating_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'probe.log'
            result = native.logged([sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(10)'], log, timeout=0.2)
            self.assertEqual(result, 'timeout')
            self.assertIn('started', log.read_text())


if __name__ == '__main__':
    unittest.main()

class BootQualificationTests(unittest.TestCase):
    def probe(self,text,outcome='success',*,occupied=False):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private=root/'private';(private/'analysis').mkdir(parents=True)
            binary=root/'synthetic-runner';binary.write_bytes(b'not executed')
            elf=root/'synthetic.elf';elf.write_bytes(b'not executed')
            (private/'analysis/native-build-stage.json').write_text(json.dumps({'binary':str(binary),'sha256':native.lab.digest(binary)}))
            def observe(command,log,**kwargs):
                self.assertNotIn('PS2_PORT_TEST_PAD_SCRIPT',kwargs['base_environment'])
                self.assertNotIn('PS2X_VU1_RECOMP',kwargs['base_environment'])
                Path(log).parent.mkdir(parents=True,exist_ok=True);Path(log).write_text(text)
                return outcome
            with patch.object(native.lab,'SOURCE',root/'empty-source'), \
                 patch.object(native.lab,'boot_executable',return_value=elf), \
                 patch.object(native,'cd_image',return_value=None), \
                 patch.object(native,'runtime_environment',return_value={'PATH':os.environ.get('PATH',''),'PS2_PORT_TEST_PAD_SCRIPT':'bad','PS2X_VU1_RECOMP':'1'}), \
                 patch.object(native,'assert_idle',side_effect=RuntimeError('occupied') if occupied else None), \
                 patch.object(native,'logged',side_effect=observe) as runner:
                if occupied:
                    with self.assertRaisesRegex(RuntimeError,'occupied'):native.boot_probe({},private)
                    runner.assert_not_called();self.assertFalse((private/'logs').exists())
                    return None
                failed=('Unimplemented PS2 stub' in text or 'missing-target' in text or outcome.startswith('exit_'))
                if failed:
                    with self.assertRaisesRegex(RuntimeError,'runtime qualification'):native.boot_probe({},private)
                else:native.boot_probe({},private)
            stage=json.loads((private/'analysis/boot-probe-stage.json').read_text())
            report=json.loads((private/'analysis/boot-probe-qualification.json').read_text())
            self.assertFalse(stage['gameplay_verified'])
            self.assertEqual(stage['probe_qualified'],not failed)
            self.assertEqual(stage['log_sha256'],native.lab.digest(private/'logs/boot-probe.log'))
            self.assertEqual(stage['qualification_sha256'],native.lab.digest(private/'analysis/boot-probe-qualification.json'))
            self.assertEqual(stage['diagnostics_passed'],report['structural_checks_passed'])
            return stage,report

    def test_incomplete_sdk_failure_is_recorded_and_blocks_probe(self):
        stage,report=self.probe('Warning: Unimplemented PS2 stub called. name=sceDmaCallback PC=0x1000\n')
        self.assertFalse(stage['diagnostics_passed'])
        self.assertEqual(report['findings'][0]['code'],'sdk_handler_unimplemented')

    def test_missing_target_blocks_probe_even_with_successful_process(self):
        stage,_=self.probe('[guest-branch:missing-target] JAL target=0x1234 caller=0x1000\n')
        self.assertEqual(stage['outcome'],'success');self.assertFalse(stage['diagnostics_passed'])

    def test_bounded_clean_observation_remains_unverified(self):
        stage,_=self.probe('synthetic clean observation\n',outcome='timeout')
        self.assertTrue(stage['diagnostics_passed']);self.assertEqual(stage['outcome'],'timeout')

    def test_abnormal_exit_and_busy_compute_never_become_acceptance(self):
        stage,_=self.probe('synthetic exit\n',outcome='exit_7')
        self.assertEqual(stage['outcome'],'exit_7')
        self.probe('',occupied=True)

class BootLaunchParityTests(unittest.TestCase):
    def fixture(self,root):
        from launch_config import directory_pin
        root=Path(root).resolve();(root/'analysis').mkdir();(root/'logs').mkdir();(root/'disc-root').mkdir()
        binary=root/'runner';binary.write_bytes(b'never executed')
        elf=root/'disc-root/TEST.ELF';elf.write_bytes(b'never executed')
        library=root/'libnative.dylib';library.write_bytes(b'never loaded')
        pack=root/'textures';pack.mkdir();(pack/'one.png').write_bytes(b'synthetic image')
        stage={'binary':str(binary),'sha256':native.lab.digest(binary),
            'frameworks':{'libnative.dylib':{'path':str(library),'sha256':native.lab.digest(library)}}}
        (root/'analysis/native-build-stage.json').write_text(json.dumps(stage))
        profile={'id':'synthetic','renderer_worker':True,'async_presentation':True,
            'native_launch':{'environment':{'PS2_PORT_HW_GS_ZERO_COPY':'1','PS2_PORT_ENHANCE_HIRES_SCANOUT':'1','PS2_PORT_ENHANCE_FXAA':'1'},
            'paths':{'PS2_PORT_TEX_PACK':{'path':'textures','sha256':directory_pin(pack)['sha256']}},
            'framework_bindings':{'PS2_PORT_PRIVATE_GS_PLUGIN':'libnative.dylib'},
            'diagnostics':False,'renderer_packet_batch':True,'renderer_deinterlace':2}}
        return root,profile,elf,library

    def test_probe_uses_declared_renderer_textures_and_profile_controls(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root,profile,elf,library=self.fixture(tmp);observed={}
            def process(command,log,**kwargs):
                observed.update(kwargs['env_patch']);Path(log).write_text('synthetic clean probe\n');return 'timeout'
            with patch.object(native.lab,'SOURCE',root/'empty'),patch.object(native.lab,'boot_executable',return_value=elf), \
                 patch.object(native,'assert_idle'),patch.object(native,'cd_image',return_value=None), \
                 patch.object(native,'logged',side_effect=process):
                native.boot_probe(profile,root)
            for key,value in {'PS2_PORT_HW_GS_ZERO_COPY':'1','PS2_PORT_ENHANCE_HIRES_SCANOUT':'1',
                'PS2_PORT_ENHANCE_FXAA':'1','PS2_PORT_GS_THREAD':'1','PS2_PORT_ASYNC_PRESENT':'1',
                'PS2_PORT_PRIVATE_GIF_BATCH':'1','PS2_PORT_PRIVATE_DEINTERLACE':'2',
                'PS2_PORT_PRIVATE_GS_PLUGIN':str(library),'PS2_PORT_TEX_PACK':str(root/'textures')}.items():
                self.assertEqual(observed[key],value)
            self.assertNotIn('PS2_PORT_PAD_TRACE',observed)
            report=json.loads((root/'analysis/boot-probe-stage.json').read_text())
            self.assertEqual(report['effective_environment'],observed);self.assertEqual(report['probe_overrides'],{})
            self.assertEqual(report['launch_assets']['PS2_PORT_TEX_PACK']['files'],1)
            self.assertFalse(report['gameplay_verified'])

    def test_missing_renderer_or_changed_assets_never_fall_back_silently(self):
        from unittest.mock import patch
        for which in ('library','texture'):
            with self.subTest(which=which),tempfile.TemporaryDirectory() as tmp:
                root,profile,elf,library=self.fixture(tmp)
                if which=='library':library.unlink()
                else:(root/'textures/one.png').write_bytes(b'changed')
                with patch.object(native.lab,'SOURCE',root/'empty'),patch.object(native.lab,'boot_executable',return_value=elf), \
                     patch.object(native,'assert_idle'),patch.object(native,'logged') as process:
                    with self.assertRaises(ValueError):native.boot_probe(profile,root)
                    process.assert_not_called()
                self.assertFalse((root/'analysis/boot-probe-stage.json').exists())

    def test_headless_treatment_is_explicitly_recorded(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root,profile,elf,_=self.fixture(tmp)
            def process(command,log,**kwargs):Path(log).write_text('synthetic headless probe\n');return 'timeout'
            with patch.object(native.lab,'SOURCE',root/'empty'),patch.object(native.lab,'boot_executable',return_value=elf), \
                 patch.object(native,'assert_idle'),patch.object(native,'cd_image',return_value=None), \
                 patch.object(native,'logged',side_effect=process):native.boot_probe(profile,root,headless=True)
            report=json.loads((root/'analysis/headless-probe-stage.json').read_text())
            self.assertEqual(report['probe_overrides']['PS2_PORT_HEADLESS'],'1')
            self.assertEqual(report['effective_environment']['PS2_PORT_HW_GS_ZERO_COPY'],'1')
            self.assertFalse(report['gameplay_verified'])

class ProvenPatchRecordRefresh(unittest.TestCase):
    def test_new_proven_stack_refreshes_stale_receipt_without_reapplying_sources(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'patches').mkdir();current=root/'patches/new.patch';current.write_text('new proven patch\n')
            record=root/'record';record.mkdir();(record/'00-old.patch').write_text('old patch\n')
            with patch.object(native.lab,'ROOT',root),patch.object(native,'RUNTIME_PATCHES',('patches/new.patch',)),patch.object(native,'_applied_stack_dir',return_value=record),patch.object(native,'_stack_already_applied',return_value=True),patch.object(native,'_git_apply',side_effect=AssertionError('Proven sources must not be peeled/reapplied')):
                native.apply_runtime_patch()
                self.assertEqual([p.name for p in record.iterdir()],['00-new.patch'])
                self.assertEqual((record/'00-new.patch').read_bytes(),current.read_bytes())
                native.apply_runtime_patch()
