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
        lab.save(self.profile, dict(id='synthetic', title='Synthetic', private_dir=str(self.private)))
        self.called=[]
    def tearDown(self): self.temp.cleanup()
    def fake(self, command, private):
        self.called.append(command)
        artifact=dict(next_game.STAGES)[command]
        lab.save(private/artifact, {'success':True, 'command':command})
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
