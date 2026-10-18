import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import recovery as r

BASE=0x1000
JR=0x03e00008

def i(op,rs,rt,imm):return op<<26|rs<<21|rt<<16|(imm&65535)
def rr(rs,rt,rd,fn,sa=0):return rs<<21|rt<<16|rd<<11|sa<<6|fn

def code(*words):return struct.pack('<'+'I'*len(words),*words)
def case(args=(),data=b'',base=0x8000):
    return dict(args=[f'{v&r.MASK64:016x}' for v in (*args,*([0]*(8-len(args))))],memory=dict(base=f'{base:08x}',hex=data.hex()))


class OracleQualificationTests(unittest.TestCase):
    def test_fixed_independent_integer_results(self):
        # Inputs and expectations are literal bit patterns; not calculated by
        # either borrowed decoder or symbolic evaluator under test.
        vectors=[
          (i(9,4,2,1),(0x7fffffff,),0xffffffff80000000),
          (i(9,4,2,-1),(0,),0xffffffffffffffff),
          (i(12,4,2,0xff),(0x1234,),0x34),
          (i(13,4,2,0xff),(0x1200,),0x12ff),
          (i(14,4,2,0xff),(0x1234,),0x12cb),
          (i(15,0,2,0xffff),(),0xffffffffffff0000),
          (rr(4,5,2,0x21),(0x7fffffff,1),0xffffffff80000000),
          (rr(4,5,2,0x23),(0,1),0xffffffffffffffff),
          (rr(4,5,2,0x24),(0xf0,0x55),0x50),
          (rr(4,5,2,0x25),(0xf0,0x55),0xf5),
          (rr(4,5,2,0x26),(0xf0,0x55),0xa5),
          (rr(4,5,2,0x2a),(0xffffffffffffffff,1),1),
          (rr(4,5,2,0x2b),(0xffffffffffffffff,1),0),
          (rr(0,4,2,0,1),(0x40000000,),0xffffffff80000000),
          (rr(0,4,2,2,1),(0xffffffffffffffff,),0x7fffffff),
          (rr(0,4,2,3,1),(0x80000000,),0xffffffffc0000000),
        ]
        for word,args,want in vectors:
            with self.subTest(word=f'{word:08x}'):
                actual=r.original(code(word,JR,0),BASE,case(args))
                self.assertEqual(int(actual['result'],16),want)
                self.assertEqual(actual['stubbed_calls'],0)

    def test_load_store_sign_width_and_delay_slot(self):
        for op,want in [(0x20,0xffffffffffffff80),(0x24,0x80),(0x21,0xffffffffffff8080),(0x25,0x8080),(0x23,0xffffffff80808080),(0x27,0x80808080)]:
            self.assertEqual(int(r.original(code(i(op,4,2,0),JR,0),BASE,case((0x8000,),bytes([128])*8))['result'],16),want)
        raw=code(i(9,5,2,0),i(0x28,4,5,0),i(0x29,4,5,2),JR,i(0x2b,4,5,4))
        actual=r.original(raw,BASE,case((0x8000,0x12345678),bytes(8)))
        self.assertEqual(actual['memory'],'7800785678563412')
        self.assertEqual(actual['writes'],[[0x8000,1,0x78],[0x8002,2,0x5678],[0x8004,4,0x12345678]])
        self.assertEqual(int(r.original(code(JR,i(9,4,2,5)),BASE,case((7,)))['result'],16),12)

    def test_faults_and_unknowns_never_become_success(self):
        for raw in [code(0x46000000,JR,0),code(0x0c000400,JR,0),code(i(4,4,5,0),JR,0),code(JR,JR),code(0x0000000c,JR,0),code(rr(4,5,2,0x20),JR,0)]:
            with self.assertRaises(ValueError):r.original(raw,BASE,case())
        for args in [(0x8001,),(0x10000000,)]:
            with self.assertRaises(ValueError):r.original(code(i(0x23,4,2,0),JR,0),BASE,case(args,bytes(8)))
        with self.assertRaises(ValueError):r.original(code(i(9,4,2,1),JR,0),BASE,case(),budget=1)
        with self.assertRaises(ValueError):r.original(code(i(9,0,16,1),JR,0),BASE,case())
        with self.assertRaises(ValueError):r.original(code(i(0x2b,4,5,0),JR,i(0x23,4,2,0)),BASE,case((0x8000,),bytes(8)))

    def test_zero_register_and_cases_do_not_leak(self):
        raw=code(i(9,4,0,3),rr(0,0,2,0x21),JR,0)
        self.assertEqual(int(r.original(raw,BASE,case((33,)))['result'],16),0)
        raw=code(i(9,5,2,0),JR,i(0x2b,4,5,0));c=case((0x8000,77),bytes(4))
        first=r.original(raw,BASE,c)
        self.assertEqual(first,r.original(raw,BASE,c));self.assertEqual(c['memory']['hex'],'00000000')


class ReadableRecoveryTests(unittest.TestCase):
    def test_actual_compiled_results_and_all_integer_operators(self):
        programs=[code(i(9,4,2,5),JR,0),code(rr(4,5,2,0x21),JR,0),code(rr(4,5,2,0x23),JR,0),
                  code(rr(4,5,2,0x24),JR,0),code(rr(4,5,2,0x25),JR,0),code(rr(4,5,2,0x26),JR,0),
                  code(rr(4,5,2,0x2a),JR,0),code(rr(4,5,2,0x2b),JR,0),
                  code(rr(0,4,2,0,3),JR,0),code(rr(0,4,2,2,3),JR,0),code(rr(0,4,2,3,3),JR,0)]
        cases=[case((x,y)) for x,y in [(0,1),(1,2),(0x7fffffff,1),(0xffffffff,1),(0x80000000,2),(0xffffffffffffffff,1),(0xfffffff012345678,0x55),(9,7)]]
        for raw in programs:
            with self.subTest(raw=raw.hex()),tempfile.TemporaryDirectory() as tmp:
                recipe=r.derive(raw,BASE);actual,host=r.run_native(recipe,cases,tmp)
                expected=[r.original(raw,BASE,c) for c in cases]
                self.assertTrue(r.compare(expected,actual)['accepted_on_cases'])
                self.assertTrue(host['binary_sha256'])

    def test_loads_and_aliased_store_snapshots(self):
        # Load before stores; overlapping destinations must not alter a later
        # source expression that originally read before the first write.
        raw=code(i(0x23,4,2,0),i(0x23,4,3,4),i(0x2b,4,3,0),JR,i(0x2b,4,2,4))
        cases=[case((0x8000,),struct.pack('<II',n,100+n)) for n in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            actual,_=r.run_native(r.derive(raw,BASE),cases,tmp)
        expected=[r.original(raw,BASE,c) for c in cases]
        self.assertTrue(r.compare(expected,actual)['accepted_on_cases'])
        self.assertEqual(actual[1]['memory'],'6500000001000000')

    def test_planted_wrong_return_store_and_missing_results_rejected(self):
        raw=code(i(9,5,2,0),JR,i(0x2b,4,5,0));cases=[case((0x8000,n),bytes(4)) for n in range(8)]
        expected=[r.original(raw,BASE,c) for c in cases];recipe=r.derive(raw,BASE)
        wrong=copy.deepcopy(recipe);wrong['expression']={'kind':'Const','value':777}
        with tempfile.TemporaryDirectory() as tmp:actual,_=r.run_native(wrong,cases,tmp)
        self.assertFalse(r.compare(expected,actual)['accepted_on_cases'])
        wrong=copy.deepcopy(recipe);wrong['stores'][0]['value']={'kind':'Const','value':777}
        with tempfile.TemporaryDirectory() as tmp:actual,_=r.run_native(wrong,cases,tmp)
        self.assertFalse(r.compare(expected,actual)['accepted_on_cases'])
        with self.assertRaises(ValueError):r.compare(expected,actual[:-1])
        with self.assertRaises(ValueError):r.compare([],[])

    def test_ast_cannot_inject_source_or_unknown_semantics(self):
        for node in [{'kind':'Const','value':'system("x")'},{'kind':'Arg','index':99},{'kind':'Binary','op':'div','left':{'kind':'Const','value':1},'right':{'kind':'Const','value':2}},{'kind':'SignExtend32','value':'native_code'}]:
            with self.assertRaises(ValueError):r.expression(node)
        raw=code(i(9,4,2,5),JR,0);self.assertIn('arg0',r.emit(r.derive(raw,BASE)))
        self.assertEqual(r.implementation()['core_revision'],'01a93a35cf6653802970c292feaa11c707a04754')


class PrivateRecoveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.private=self.root/'one';self.private.mkdir()
        self.shared=self.root/'.private/recovery-bank'
        self.patch=patch.object(r,'SHARED_BANK',self.shared);self.patch.start();self.addCleanup(self.patch.stop)

    def spec(self,private,raw,entry=BASE,data_base=0x8000):
        (private/'disc-root').mkdir(exist_ok=True)
        header=struct.pack('<16sHHIIIIIHHHHHH',b'\x7fELF\x01\x01\x01'+bytes(9),2,8,1,entry,52,0,0,52,32,1,40,0,0)
        ph=struct.pack('<8I',1,0x100,entry,entry,len(raw),len(raw),5,4)
        image=header+ph+bytes(256-84)+raw
        (private/'disc-root/TEST.ELF').write_bytes(image)
        (private/'disc-root/SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
        profile=dict(id=private.name,expected_sha256=r.sha(image))
        spec=dict(schema=1,executable_sha256=r.sha(image),function=dict(address=f'{entry:08x}',bytes=len(raw)),
                  cases=[case((data_base,n),struct.pack('<II',n,100+n),data_base) for n in range(8)])
        (private/'spec.json').write_text(json.dumps(spec))
        return profile,spec

    def test_recover_verify_retain_reuse_across_executables_and_targets(self):
        raw=code(i(0x23,4,2,0),JR,i(9,2,2,5))
        profile,_=self.spec(self.private,raw)
        first=r.run(profile,self.private,'spec.json','first','@shared')
        self.assertEqual(first['status'],'agreement_on_cases');self.assertEqual(first['matched'],8)
        self.assertFalse(first['promoted_to_runtime']);key=first['library_record']
        second=self.root/'two';second.mkdir();other,_=self.spec(second,raw,entry=0x3000)
        self.assertNotEqual(first['inputs']['executable_sha256'],other['expected_sha256'])
        self.assertEqual(r.matches(second,raw,'@shared')[0]['match'],'exact_code')
        reused=r.run(other,second,'spec.json','second','@shared',key)
        self.assertEqual(reused['status'],'agreement_on_cases');self.assertEqual(reused['provenance']['candidate'],key)
        self.assertEqual((second/'analysis/function-recovery/second').stat().st_mode&0o777,0o700)
        self.assertFalse(reused['universal_equivalence'])
        with self.assertRaises(FileExistsError):r.run(other,second,'spec.json','second','@shared',key)
        self.assertEqual((self.private/'disc-root/TEST.ELF').read_bytes()[-len(raw):],raw)

    def test_masked_relocation_requires_explicit_mapping_and_fresh_native_judge(self):
        a=code(i(15,0,8,1),i(13,8,8,0x8000),i(0x23,8,2,0),JR,0)
        b=code(i(15,0,8,2),i(13,8,8,0x8000),i(0x23,8,2,0),JR,0)
        profile,_=self.spec(self.private,a,data_base=0x18000)
        first=r.run(profile,self.private,'spec.json','source','@shared');key=first['library_record']
        second=self.root/'two';second.mkdir();other,_=self.spec(second,b,entry=0x3000,data_base=0x28000)
        self.assertEqual(r.matches(second,b,'@shared')[0]['match'],'masked_candidate')
        with self.assertRaises(RuntimeError):r.run(other,second,'spec.json','no-binding','@shared',key)
        failed=json.loads((second/'analysis/function-recovery/no-binding/report.json').read_text())
        self.assertFalse(failed['accepted_on_cases']);self.assertNotIn('library_record',failed)
        (second/'bindings.json').write_text(json.dumps({'00018000':'00028000'}))
        passed=r.run(other,second,'spec.json','bound','@shared',key,'bindings.json')
        self.assertTrue(passed['accepted_on_cases']);self.assertEqual(passed['matched'],8)
        with self.assertRaises(ValueError):r.relocate(r.derive(a,BASE),{'00077700':'00088800'})

    def test_normalization_does_not_mask_after_register_clobber(self):
        a=code(i(15,0,8,1),rr(4,5,8,0x21),i(0x23,8,2,4),JR,0)
        b=code(i(15,0,8,2),rr(4,5,8,0x21),i(0x23,8,2,8),JR,0)
        self.assertNotEqual(r.normalize(a),r.normalize(b))

    def test_private_inputs_case_coverage_and_record_tampering(self):
        raw=code(i(9,5,2,5),JR,0);profile,spec=self.spec(self.private,raw)
        spec['cases']=spec['cases'][:2];(self.private/'spec.json').write_text(json.dumps(spec))
        with self.assertRaises(ValueError):r.run(profile,self.private,'spec.json','few')
        profile,spec=self.spec(self.private,raw);spec['cases']=[spec['cases'][0]]*8
        (self.private/'spec.json').write_text(json.dumps(spec))
        with self.assertRaises(ValueError):r.load_spec(profile,self.private,'spec.json')
        profile,_=self.spec(self.private,raw)
        with self.assertRaises(ValueError):r.load_spec(profile,self.private,'../one/spec.json')
        (self.private/'alias.json').symlink_to(self.private/'spec.json')
        with self.assertRaises(ValueError):r.load_spec(profile,self.private,'alias.json')
        first=r.run(profile,self.private,'spec.json','first','@shared')
        path=self.shared/(first['library_record']+'.json');path.write_text(path.read_text()+' ')
        with self.assertRaises(ValueError):r.matches(self.private,raw,'@shared')

    def test_drift_during_compiled_comparison_never_promotes(self):
        raw=code(i(9,5,2,5),JR,0);profile,_=self.spec(self.private,raw)
        execute=r.run_native
        def drift(*a,**kw):
            value=execute(*a,**kw)
            path=self.private/'spec.json';path.write_text(path.read_text()+' ')
            return value
        with patch.object(r,'run_native',side_effect=drift):
            with self.assertRaisesRegex(ValueError,'changed during'):r.run(profile,self.private,'spec.json','drift','@shared')
        self.assertFalse(self.shared.exists())
        self.assertEqual(json.loads((self.private/'analysis/function-recovery/drift/report.json').read_text())['status'],'failed')

    def test_fixed_oracle_fixture_and_implementation_pins(self):
        self.assertEqual(r.qualify_oracle()['cases'],25)
        raw=code(i(9,4,2,5),JR,0);bad=r.derive(raw,BASE)
        bad['stores']=[{'size':16,'address':{'kind':'Const','value':0},'value':{'kind':'Const','value':0}}]
        with self.assertRaises(ValueError):r.emit(bad)


class RecoveryCliTests(unittest.TestCase):
    setUp = PrivateRecoveryWorkflowTests.setUp
    spec = PrivateRecoveryWorkflowTests.spec
    def test_real_shared_cli_executes_recovery_and_rejects_wrong_revision(self):
        raw=code(i(9,5,2,5),JR,0);profile,spec=self.spec(self.private,raw)
        profile.update(private_dir=str(self.private),private_dir_absolute_opt_in=True)
        path=self.root/'profile.json';path.write_text(json.dumps(profile))
        command=[sys.executable,str(r.ROOT/'run.py'),'--profile',str(path),'recover-function','--spec','spec.json','--name','cli']
        ran=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertEqual(ran.returncode,0,ran.stderr)
        self.assertTrue(json.loads(ran.stdout)['accepted_on_cases'])
        profile['expected_sha256']='0'*64;path.write_text(json.dumps(profile))
        command[-1]='wrong-revision'
        ran=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(ran.returncode,0);self.assertFalse((self.private/'analysis/function-recovery/wrong-revision').exists())

    def test_cpp_and_macos_targets_expose_same_commands(self):
        from target_cli import check_target
        for command in ('oracle-check','recover-function','find-recovery-candidates'):
            check_target('cpp',command,'Linux');check_target('macos',command,'Darwin')
