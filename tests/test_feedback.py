import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import feedback as f
import pipeline as lab
from target_cli import check_target


def elf():
    code=struct.pack('<4I',0x24080001,0x03e00008,0,0)
    header=struct.pack('<16sHHIIIIIHHHHHH',b'\x7fELF\x01\x01\x01'+bytes(9),2,8,1,0x100000,52,0,0,52,32,2,40,0,0)
    ph=struct.pack('<8I',1,0x100,0x100000,0,len(code),0x20,5,4)
    data=struct.pack('<8I',1,0x110,0x200000,0,4,4,6,4)
    return header+ph+data+bytes(0x100-116)+code+b'data'


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'analysis').mkdir();(self.root/'disc-root').mkdir();(self.root/'logs').mkdir()
        self.elf=self.root/'disc-root/TEST.ELF';self.elf.write_bytes(elf())
        (self.root/'disc-root/SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
        self.profile={'id':'synthetic','boot':'TEST.ELF','expected_sha256':f.digest(elf())}
        self.base=self.root/'analysis/recomp.toml'
        self.base.write_text('[general]\ninput = '+json.dumps(str(self.elf))+'\noutput = "generated"\nentry_points = [\n "old@0x100004",\n]\n[patches]\nskip = []\n')
        self.log=self.root/'logs/one.log'
        self.log.write_text('[guest-branch:missing-target] kind=call op=jalr source=0x100004 target=0x100000 pc=0x100004\n')

    def prepare(self):return f.prepare(self.profile,self.root,'logs/one.log','first')

    def test_native_and_web_targets_deduplicate_and_reject_alignment_data_bss(self):
        text=self.log.read_text()+self.log.read_text()+'[ee] no translated code at 0x00100004 (ra 0)\n'
        for at in [0x100001,0x100002,0x200000,0x100010,0x80000000]:
            text+=f'[ee] no translated code at 0x{at:08X} (ra 0)\n'
        accepted,rejected=f.candidates(elf(),text)
        self.assertEqual([x['address'] for x in accepted],['00100000','00100004'])
        self.assertEqual(len(rejected),5)
        self.assertEqual(accepted[0]['file_word'],'01000824')
        self.assertEqual(f.candidates(elf(),'100000\nsource=0x100000\n'),([],[]))

    def test_real_backend_entry_syntax_and_unrelated_settings_preserved(self):
        before=self.base.read_text();render=f.config_with_hints(before,['feedback_00100000@0x00100000'])
        parsed=tomllib.loads(render)
        self.assertIn('feedback_00100000@0x00100000',parsed['general']['entry_points'])
        self.assertEqual(parsed['patches'],tomllib.loads(before)['patches'])
        self.assertEqual(f.config_with_hints(render,['feedback_00100000@0x00100000']),render)
        self.assertIn('entry_points',tomllib.loads(f.config_with_hints('[general]\ninput="x"\n',[]))['general'])

    def test_revalidate_and_explicit_no_gameplay_claim(self):
        before=self.base.read_bytes();r=self.prepare();path,receipt=f.consume(self.profile,self.root,'first')
        self.assertEqual(r['status'],'candidates_ready');self.assertFalse(r['playable_certified'])
        self.assertFalse(r['runtime_code_identity_verified']);self.assertFalse(r['module_generation_verified'])
        self.assertEqual(receipt['candidates'],1);self.assertTrue(path.is_file());self.assertEqual(self.base.read_bytes(),before)
        self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        with self.assertRaises(FileExistsError):self.prepare()

    def test_each_source_drift_is_rejected_before_translation(self):
        for which in ('elf','config','log','candidate','report'):
            with self.subTest(which=which):
                r=self.prepare();out=self.root/'analysis/runtime-feedback/first'
                path={'elf':self.elf,'config':self.base,'log':self.log,'candidate':out/'recomp.toml','report':out/'report.json'}[which]
                old=path.read_bytes()
                if which=='report':
                    r['compiler_hints']=['injected@0x100008'];path.write_text(json.dumps(r))
                else:path.write_bytes(old+b'\n')
                with self.assertRaises(ValueError):f.consume(self.profile,self.root,'first')
                path.write_bytes(old)
                import shutil
                shutil.rmtree(out)

    def test_profiles_and_lock_cannot_silently_change(self):
        self.prepare();other=dict(self.profile,id='other')
        with self.assertRaises(ValueError):f.consume(other,self.root,'first')
        other=dict(self.profile,expected_sha256='0'*64)
        with self.assertRaises(ValueError):f.consume(other,self.root,'first')
        out=self.root/'analysis/runtime-feedback/first/report.json';r=json.loads(out.read_text());r['toolchain_lock_sha256']='0'*64;out.write_text(json.dumps(r))
        with self.assertRaises(ValueError):f.consume(self.profile,self.root,'first')

    def test_implementation_drift_requires_new_feedback(self):
        self.prepare()
        out=self.root/'analysis/runtime-feedback/first/report.json'
        r=json.loads(out.read_text());r['implementation_sha256']='0'*64;out.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'implementation changed'):
            f.consume(self.profile,self.root,'first')

    def test_no_new_targets_is_not_accepted(self):
        self.log.write_text('[ee] halted: deadlock\n');r=self.prepare()
        self.assertEqual(r['status'],'no_eligible_targets');self.assertFalse(r['playable_certified'])
        with self.assertRaises(ValueError):f.consume(self.profile,self.root,'first')

    def test_bad_segments_and_budget_fail_closed(self):
        raw=bytearray(elf());struct.pack_into('<I',raw,52+20,1)
        with self.assertRaises(ValueError):f.candidates(bytes(raw),self.log.read_text())
        raw=bytearray(elf());struct.pack_into('<I',raw,28,0xfffffff0)
        with self.assertRaises(ValueError):f.candidates(bytes(raw),'')
        with patch.object(f,'MAX_TARGETS',0):
            with self.assertRaises(ValueError):self.prepare()
        with patch.object(f,'MAX_LOG',1):
            with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.root/'analysis/runtime-feedback/first').exists())

    def test_private_paths_and_symlinked_parents_rejected(self):
        for rel in ('../one.log','/tmp/one.log'):
            with self.assertRaises(ValueError):f.prepare(self.profile,self.root,rel,'first')
        (self.root/'alias').symlink_to(self.root/'logs',target_is_directory=True)
        with self.assertRaises(ValueError):f.prepare(self.profile,self.root,'alias/one.log','first')
        (self.root/'analysis/runtime-feedback').symlink_to(self.root/'logs',target_is_directory=True)
        with self.assertRaises(ValueError):self.prepare()

    def test_translation_consumes_verified_config_and_keeps_acceptance_gates(self):
        self.prepare();out=self.root/'analysis/runtime-feedback/first/recomp.toml'
        def execute(args,*a,**kw):
            self.assertEqual(args[-1],out.resolve())
            (self.root/'generated').mkdir();(self.root/'generated/a.cpp').write_text('// own synthetic fixture')
            (self.root/'logs/translate.log').write_text('not a successful compiler summary')
        with patch.object(lab,'run',execute),patch('knowledge.apply_translation',return_value={'totals':{'instructions':0,'corrected':0,'files_changed':0},'bank_revision':3,'bank_sha256':'0'*64}),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'Translation not accepted'):lab.translate(self.profile,self.root,'first')
        stage=json.loads((self.root/'analysis/translation-stage.json').read_text())
        self.assertFalse(stage['success']);self.assertEqual(stage['runtime_target_feedback']['candidates'],1)
        self.assertFalse(stage['runtime_verified'])

    def test_shared_cli_forwards_feedback_and_prepare_keeps_default(self):
        with patch.object(lab,'load_profile',return_value=(self.profile,self.root)),patch.object(lab,'translate') as translate:
            with patch.object(sys,'argv',['pipeline.py','--profile','profile.json','translate','--feedback','first']):
                lab.main(target='cpp')
            translate.assert_called_once_with(self.profile,self.root,'first')
            translate.reset_mock()
            with patch.object(lab,'verify'),patch.object(lab,'inspect'),patch.object(lab,'analyze'),patch.object(sys,'argv',['pipeline.py','--profile','profile.json','prepare']):
                lab.main(target='cpp')
            translate.assert_called_once_with(self.profile,self.root)

    def test_both_products_share_feedback(self):
        check_target('cpp','collect-runtime-targets','Linux');check_target('macos','collect-runtime-targets','Darwin')
        for name in ['../escape','UPPER','a/b']:
            with self.assertRaises(ValueError):f.prepare(self.profile,self.root,'logs/one.log',name)

class BootRecoveryTests(unittest.TestCase):
    setUp=FeedbackTests.setUp
    def probe_stage(self):
        binary=self.root/'synthetic-runner';binary.write_bytes(b'not executed')
        build=self.root/'analysis/native-build-stage.json'
        build.write_text(json.dumps({'binary':str(binary),'sha256':lab.digest(binary)}))
        stage={'log':str(self.log.resolve()),'log_sha256':lab.digest(self.log),'build_stage_sha256':lab.digest(build),
            'binary_sha256':lab.digest(binary),'executable_sha256':lab.digest(self.elf),
            'toolchain_lock_sha256':lab.digest(lab.ROOT/'toolchain.lock.json'),'probe_qualified':False}
        path=self.root/'analysis/boot-probe-stage.json';path.write_text(json.dumps(stage))
        return path,binary

    def test_bound_probe_automatically_prepares_idempotent_candidates(self):
        stage,_=self.probe_stage();before=self.base.read_bytes()
        result=f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        self.assertEqual(result['status'],'candidates_prepared')
        self.assertEqual(len(result['candidates']),1)
        self.assertFalse(result['automatic_translation_allowed'])
        self.assertFalse(result['runtime_code_identity_verified'])
        self.assertEqual(result['probe_sha256'],lab.digest(stage))
        again=f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        self.assertEqual(again['feedback_name'],result['feedback_name']);self.assertTrue(again['reused'])
        self.assertEqual(self.base.read_bytes(),before)

    def test_changed_bound_probe_inputs_are_rejected_before_candidate_creation(self):
        for which in ('log','binary','elf','build'):
            with self.subTest(which=which):
                _,binary=self.probe_stage()
                path={'log':self.log,'binary':binary,'elf':self.elf,'build':self.root/'analysis/native-build-stage.json'}[which]
                old=path.read_bytes();path.write_bytes(old+b'changed')
                with self.assertRaises(ValueError):f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
                path.write_bytes(old)
                self.assertFalse((self.root/'analysis/runtime-feedback').exists())

    def test_overlay_or_nonexecutable_target_stays_blocked_from_translation(self):
        self.log.write_text('[guest-branch:missing-target] JAL target=0x200000 caller=0x100004\n')
        self.probe_stage()
        result=f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        self.assertEqual(result['status'],'no_safe_static_candidates')
        self.assertEqual(result['candidates'],[]);self.assertEqual(len(result['rejected']),1)
        self.assertFalse(result['automatic_translation_allowed'])
        again=f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        self.assertTrue(again['reused'])
        with self.assertRaises(ValueError):f.consume(self.profile,self.root,result['feedback_name'])

    def test_clean_or_outside_probe_does_not_seed_feedback(self):
        stage,_=self.probe_stage();value=json.loads(stage.read_text());value['probe_qualified']=True
        stage.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'no failed'):f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        value['probe_qualified']=False;value['log']=str(self.root.parent/'outside.log');stage.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'outside'):f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')

    def test_native_probe_failure_automatically_writes_recovery_candidates(self):
        import native
        self.probe_stage()
        before=self.base.read_bytes()
        def observed(command,log,**kwargs):
            Path(log).write_text(self.log.read_text())
            return 'success'
        with patch.object(native.lab,'SOURCE',self.root/'empty'), \
             patch.object(native,'assert_idle'),patch.object(native,'cd_image',return_value=None), \
             patch.object(native,'runtime_environment',return_value={}),patch.object(native,'logged',side_effect=observed):
            with self.assertRaisesRegex(RuntimeError,'runtime qualification'):
                native.boot_probe(self.profile,self.root)
        plan=json.loads((self.root/'analysis/boot-probe-recovery.json').read_text())
        self.assertEqual(plan['status'],'candidates_prepared')
        self.assertFalse(plan['automatic_translation_allowed'])
        self.assertEqual(plan['candidates'][0]['address'],'00100000')
        self.assertEqual(self.base.read_bytes(),before)

    def test_later_boot_log_does_not_destroy_prior_candidate_evidence(self):
        self.probe_stage()
        result=f.plan_boot_recovery(self.profile,self.root,'analysis/boot-probe-stage.json')
        original=self.log.read_bytes();self.log.write_text('later unrelated probe\n')
        _,receipt=f.consume(self.profile,self.root,result['feedback_name'])
        self.assertEqual(receipt['candidates'],1)
        evidence=self.root/result['probe_evidence']
        self.assertEqual((evidence/'run.log').read_bytes(),original)
        self.assertEqual(f.digest((evidence/'probe.json').read_bytes()),result['probe_sha256'])
