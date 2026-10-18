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
