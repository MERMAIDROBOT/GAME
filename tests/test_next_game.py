"""Asset-free workflow regressions: failures and stale artifacts must not pass."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as lab
import next_game
from test_pipeline import minimal_elf


class NextGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = self.root/'private'
        self.disc = self.private/'disc-root'; self.disc.mkdir(parents=True)
        (self.disc/'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
        (self.disc/'TEST.ELF').write_bytes(minimal_elf())
        self.profile = self.root/'game.json'
        lab.save(self.profile, dict(id='synthetic', title='Synthetic', private_dir=str(self.private),
                                    private_dir_absolute_opt_in=True))
        self.called=[]
    def tearDown(self): self.temp.cleanup()
    def fake(self, command, private):
        self.called.append(command)
        artifact=dict(next_game.STAGES)[command]
        lab.save(private/artifact, {'success':True, 'command':command})
        if command == 'translate':
            lab.save(private/artifact, {'success':True, 'accepted':True, 'blockers':[], 'command':command,
                                        'counts':{'processed':1,'recompiled':1,'stubs':0,'skipped':0,'decode_failures':0,'warnings':0,'errors':0}})
        if command == 'analyze':
            (private/'analysis/recomp.toml').write_text('[general]\n')
        if command == 'translate':
            (private/'generated').mkdir(exist_ok=True)
            (private/'generated/a.cpp').write_text('void example() {}\n')
    def run_workflow(self, **kwargs):
        with patch.object(lab, 'WORK', self.root/'work'):
            return next_game.run(self.profile, 'first', runner=self.fake, **kwargs)
    def test_plan_has_no_mutating_stages_and_cannot_certify_playable(self):
        r=self.run_workflow()
        self.assertEqual(self.called, [])
        self.assertEqual(r['status'], 'planned')
        self.assertFalse(r['playable_certified'])
    def test_compiler_failure_has_a_durable_preflight_without_false_run_contract(self):
        with patch.object(lab,'WORK',self.root/'work'),patch('tool_state.ensure',side_effect=RuntimeError('unknown source edit')):
            with self.assertRaisesRegex(RuntimeError,'unknown source edit'):
                next_game.run(self.profile,'compiler-failed',through='translate',execute=True)
        target=self.private/'next-game-runs/compiler-failed'
        receipt=json.loads((target/'compiler-preflight.json').read_text())
        self.assertEqual(receipt['status'],'blocked')
        self.assertEqual(receipt['stage'],'compiler-refresh')
        self.assertFalse(receipt['playable_certified'])
        self.assertFalse((target/'report.json').exists())
        self.assertEqual(self.called,[])
    def test_new_profile_discovers_and_pins_boot_revision(self):
        path=self.root/'another-title.json'
        p=next_game.create_profile(path, 'Another title', self.disc)
        self.assertEqual(p['expected_executable'],'TEST.ELF')
        self.assertEqual(p['expected_sha256'],lab.digest(self.disc/'TEST.ELF'))
        with self.assertRaisesRegex(ValueError,'exists'):
            next_game.create_profile(path,'Replacement',self.disc)
    def test_success_and_resume_do_not_retranslate_valid_sources(self):
        r=self.run_workflow(execute=True, through='translate')
        self.assertEqual(r['status'], 'translation_complete_integration_required')
        self.called.clear()
        self.run_workflow(execute=True, through='translate', resume=True)
        self.assertEqual(self.called, ['verify'])
    def test_tampered_generated_code_invalidates_downstream(self):
        self.run_workflow(execute=True, through='translate')
        (self.private/'generated/a.cpp').write_text('corrupted')
        self.called.clear()
        self.run_workflow(execute=True, through='translate', resume=True)
        self.assertEqual(self.called, ['verify', 'translate', 'check-generated'])
    def test_changed_disc_revision_refuses_resume(self):
        self.run_workflow()
        (self.disc/'TEST.ELF').write_bytes(minimal_elf(entry=0x200008))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run_workflow(resume=True)
    def test_added_generated_file_invalidates_translation(self):
        self.run_workflow(execute=True, through='translate')
        (self.private/'generated/extra.cpp').write_text('stale injected unit')
        self.called.clear()
        self.run_workflow(execute=True, through='translate', resume=True)
        self.assertIn('translate', self.called)
        self.assertIn('check-generated', self.called)
    def test_decode_failure_blocks_syntax_and_acceptance(self):
        def decode_failure(command, private):
            self.fake(command, private)
            if command == 'translate':
                lab.save(private/'analysis/translation-stage.json', {'success':True, 'counts':{'decode_failures':1}})
        with patch.object(lab, 'WORK', self.root/'work'):
            r=next_game.run(self.profile,'first',execute=True,through='translate',runner=decode_failure)
        self.assertEqual(r['status'],'blocked')
        self.assertNotIn('check-generated',self.called)
    def test_failure_keeps_ticket_and_never_runs_later_stages(self):
        def broken(command, private):
            if command == 'analyze': raise RuntimeError('unsupported instruction')
            self.fake(command, private)
        with patch.object(lab,'WORK',self.root/'work'):
            r=next_game.run(self.profile,'first',execute=True,through='translate',runner=broken)
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(r['ticket']['subsystem'], 'analyze')
        self.assertNotIn('translate', self.called)
    def test_missing_stage_artifact_cannot_pass(self):
        with patch.object(lab,'WORK',self.root/'work'):
            r=next_game.run(self.profile,'first',execute=True,runner=lambda c,p:None)
        self.assertEqual(r['status'],'blocked')
    def test_real_synthetic_disc_intake_and_corruption_ticket(self):
        import contextlib, io
        source=self.root/'mounted'; source.mkdir()
        (source/'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
        (source/'TEST.ELF').write_bytes(minimal_elf())
        (source/'fixture.irx').write_bytes(b'synthetic-module-not-a-retail-file')
        def actual(command, private):
            profile,_=lab.load_profile(self.profile)
            if command=='import': lab.import_disc(source,profile,private)
            elif command=='knowledge-plan':
                from knowledge import plan
                plan(profile, private)
            elif command=='code-census':
                from code_census import census
                census(private, profile)
            else: getattr(lab,command)(profile,private)
        with patch.object(lab,'WORK',self.root/'work'),contextlib.redirect_stdout(io.StringIO()):
            r=next_game.run(self.profile,'first',source=source,execute=True,runner=actual)
            self.assertEqual(r['status'],'intake_complete_integration_required')
            self.assertEqual(len(r['inventory']['iop_modules']),1)
            (self.private/'disc-root/fixture.irx').write_bytes(b'corrupt')
            # Without a mounted source, verify must fail rather than recopy.
            r=next_game.run(self.profile,'corrupt',execute=True,runner=actual)
            self.assertEqual(r['status'],'blocked')
            self.assertEqual(r['ticket']['subsystem'],'verify')
    def test_stale_lock_blocks_overlapping_run(self):
        import fcntl
        work=self.root/'work'; work.mkdir()
        with (work/'next-game.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'pipeline slot'):
                self.run_workflow(execute=True)

    def test_automatic_qualification_blocks_old_signed_branch_even_after_translation_success(self):
        def obsolete(command,private):
            self.fake(command,private)
            if command=='translate':
                (private/'generated/a.cpp').write_text('// 0x1000: 0x1d000001 bgtz\nif(GPR_S32(ctx, 8)>0){}\n')
        with patch.object(lab,'WORK',self.root/'work'):
            report=next_game.run(self.profile,'obsolete',through='translate',execute=True,runner=obsolete)
        self.assertEqual(report['status'],'blocked')
        self.assertEqual(report['ticket']['subsystem'],'translate')
        self.assertFalse(report['playable_certified'])

    def test_mac_package_plan_orders_tests_before_packaging(self):
        r=self.run_workflow(through='package')
        self.assertEqual([x['command'] for x in r['plan']][-3:],
                         ['build-native','test-runtime','package-local'])
        self.assertFalse(r['playable_certified'])
        self.assertEqual(self.called,[])

    def test_native_resume_products_are_checked_not_only_receipts(self):
        analysis=self.private/'analysis';analysis.mkdir()
        runner=self.private/'runner';runner.write_bytes(b'native fixture')
        library=self.private/'libgpu.dylib';library.write_bytes(b'library fixture')
        stage=dict(binary=str(runner),sha256=lab.digest(runner),frameworks={'libgpu.dylib':dict(path=str(library),sha256=lab.digest(library))})
        lab.save(analysis/'native-build-stage.json',stage)
        before=next_game.outputs(self.private,'build-native','analysis/native-build-stage.json')
        self.assertTrue(any(k.startswith('native-runner:') for k in before))
        library.write_bytes(b'changed behind an unchanged receipt')
        with self.assertRaisesRegex(ValueError,'Changed frameworks'):next_game.outputs(self.private,'build-native','analysis/native-build-stage.json')
        runner.unlink()
        with self.assertRaisesRegex(ValueError,'Missing/unsafe native-runner'):next_game.outputs(self.private,'build-native','analysis/native-build-stage.json')

    def test_packaged_products_and_configuration_are_resume_inputs(self):
        analysis=self.private/'analysis';analysis.mkdir()
        app=self.root/'Synthetic (Debug).app';c=app/'Contents'
        (c/'MacOS').mkdir(parents=True);(c/'Resources').mkdir();(c/'Frameworks').mkdir()
        fields={'build_sha256':'MacOS/ps2EntryRunner','launcher_sha256':'MacOS/LocalLauncher','info_sha256':'Info.plist','configuration_sha256':'Resources/RunConfiguration.plist'}
        stage=dict(app=str(app))
        for key,relative in fields.items():
            p=c/relative;p.write_bytes(('fixture '+key).encode());stage[key]=lab.digest(p)
        lib=c/'Frameworks/libgpu.dylib';lib.write_bytes(b'library');stage['frameworks']={'libgpu.dylib':lab.digest(lib)}
        lab.save(analysis/'local-package-stage.json',stage)
        with patch.object(lab,'ROOT',self.root/'tool'):
            r=next_game.outputs(self.private,'package-local','analysis/local-package-stage.json')
            self.assertTrue(any(k.startswith('app-configuration_sha256:') for k in r))
            outside=self.root/'outside.dylib';outside.write_bytes(b'library');lib.unlink();lib.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'unsafe'):next_game.outputs(self.private,'package-local','analysis/local-package-stage.json')


class NativeResumeContract(unittest.TestCase):
    setUp=NextGameTests.setUp
    tearDown=NextGameTests.tearDown
    fake=NextGameTests.fake
    def test_runtime_verdict_does_not_recompile_or_retranslate_on_resume(self):
        def execute(command,private):
            if command not in {'build-native','test-runtime'}:return self.fake(command,private)
            self.called.append(command);path=private/'analysis/native-build-stage.json'
            if command=='build-native':
                binary=private/'runner';binary.write_bytes(b'synthetic runner');lab.save(path,dict(binary=str(binary),sha256=lab.digest(binary),runtime_verified=False))
            else:
                stage=json.loads(path.read_text());stage.update(runtime_verified=True,conformance_receipt=dict(path='synthetic-runtime.json',sha256='a'*64));lab.save(path,stage);lab.save(private/'analysis/runtime-tests-stage.json',dict(success=True))
        with patch.object(lab,'WORK',self.root/'work'):
            first=next_game.run(self.profile,'native',through='native',execute=True,runner=execute)
            self.assertEqual(first['status'],'native_complete_gameplay_unverified');self.called.clear()
            next_game.run(self.profile,'native',through='native',execute=True,runner=execute,resume=True)
        self.assertEqual(self.called,['verify'])
    def test_unknown_mutation_is_still_a_build_contract_change(self):
        analysis=self.private/'analysis';analysis.mkdir();binary=self.private/'runner';binary.write_bytes(b'fixture');path=analysis/'native-build-stage.json'
        stage=dict(binary=str(binary),sha256=lab.digest(binary),runtime_verified=False);lab.save(path,stage)
        before=next_game.outputs(self.private,'build-native','analysis/native-build-stage.json')
        stage.update(runtime_verified=True,conformance_receipt=dict(path='runtime.json',sha256='b'*64));lab.save(path,stage)
        self.assertEqual(next_game.outputs(self.private,'build-native','analysis/native-build-stage.json'),before)
        stage['unknown_changed_field']='not runtime owned';lab.save(path,stage)
        self.assertNotEqual(next_game.outputs(self.private,'build-native','analysis/native-build-stage.json'),before)

    def test_revoked_runtime_verdict_invalidates_runtime_stage(self):
        analysis=self.private/'analysis';analysis.mkdir();lab.save(analysis/'native-build-stage.json',dict(source_graph_recipe_sha256='a'*64,runtime_verified=False));lab.save(analysis/'runtime-tests-stage.json',dict(success=True))
        with self.assertRaisesRegex(ValueError,'conformance before packaging'):next_game.outputs(self.private,'test-runtime','analysis/runtime-tests-stage.json')



class StagePlan(unittest.TestCase):
    def test_translate_plan_keeps_every_stage_through_the_syntax_check(self):
        import next_game
        names = [c for c, _ in next_game.stages_through('translate')]
        self.assertEqual(names, [c for c, _ in next_game.STAGES])
        self.assertEqual(names[-1], 'check-generated')
        self.assertIn('code-census', names)

    def test_intake_plan_ends_with_the_knowledge_plan(self):
        import next_game
        names = [c for c, _ in next_game.stages_through('intake')]
        self.assertEqual(names[-1], 'knowledge-plan')
        self.assertIn('code-census', names)
        self.assertNotIn('translate', names)
