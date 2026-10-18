import copy
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
import vu_dependencies as deps
from target_cli import check_target

NOP=0x2ff


def add(fd,fs,ft,mask=15,op=0x28):
    return op|fd<<6|fs<<11|ft<<16|mask<<21


def image(words=()):
    raw=bytearray(struct.pack('<II',0,NOP)*2048)
    for i,w in enumerate(words):struct.pack_into('<II',raw,i*8,0,w)
    struct.pack_into('<II',raw,len(words)*8,0,NOP|0x40000000)
    return bytes(raw)


def entry0(report):
    return next(e for e in report['entries'] if e['pc']=='0000')


def vf_inputs(entry):
    return {(x['register'],'xyzw'.index(x['lane'])) for x in entry['inputs'] if x['kind']=='vf'}


class VuDependencyTests(unittest.TestCase):
    def test_lane_reads_full_overwrite_partial_overwrite_and_q(self):
        self.assertEqual(vf_inputs(entry0(deps.analyze_image(image([add(3,1,2)])))),
                         {(r,c) for r in (1,2) for c in range(4)})
        full=entry0(deps.analyze_image(image([add(1,0,0),add(3,1,2)])))
        self.assertEqual(vf_inputs(full),{(2,c) for c in range(4)})
        partial=entry0(deps.analyze_image(image([add(1,0,0,8),add(3,1,2)])))
        self.assertEqual(vf_inputs(partial),{(1,c) for c in (1,2,3)}|{(2,c) for c in range(4)})
        q=entry0(deps.analyze_image(image([add(3,1,0,15,0x20)])))
        self.assertIn({'kind':'q'},q['inputs'])

    def test_report_does_not_certify_parallelism_memory_or_hardware(self):
        r=deps.analyze_image(image([add(3,1,2)]))
        self.assertFalse(r['runtime_optimization_eligible'])
        self.assertFalse(r['hardware_verified'])
        self.assertFalse(r['arbitrary_entry_coverage'])
        self.assertEqual(r['memory_dependencies'],'not_analyzed')
        self.assertFalse(r['termination_verified'])

    def test_budget_exhaustion_is_unknown_not_an_empty_dependency_set(self):
        r=deps.analyze_image(image([add(3,1,2)]),total_budget=0)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(all(e['status']=='unknown' and e['inputs'] is None for e in r['entries']))
        self.assertEqual(r['states_examined'],0)
        limited=deps.analyze_image(image([add(3,1,2)]),total_budget=2)
        self.assertLessEqual(limited['states_examined'],2)
        for n in [-1,True,100001]:
            with self.assertRaises(ValueError):deps.analyze_image(image(),total_budget=n)

    def test_micro_pc_wrap_in_last_ebit_delay_slot(self):
        raw=bytearray(struct.pack('<II',0,NOP)*2048)
        struct.pack_into('<II',raw,0x3ff8,0,NOP|0x40000000)
        labels,pairs=deps.core.analyse(bytes(raw))
        self.assertTrue(all(0<=pc<16384 for pc in pairs))
        self.assertEqual(pairs[0].pc,0)
        r=deps.analyze_image(bytes(raw))
        self.assertTrue(r['decoded_pc_in_bounds'])
        self.assertLessEqual(r['decoded_pairs'],2048)

    def test_reserved_indirect_and_branch_delay_assumptions_are_not_promoted(self):
        unsupported=bytearray(image());struct.pack_into('<II',unsupported,0,0,0xffffffff)
        self.assertEqual(deps.analyze_image(bytes(unsupported))['status'],'unsupported')
        indirect=bytearray(image());struct.pack_into('<II',indirect,0,0x24<<25,NOP)
        r=deps.analyze_image(bytes(indirect))
        self.assertEqual(r['reason'],'indirect_branch_targets_not_qualified')
        self.assertEqual(r['entries'],[])
        slot=bytearray(image());struct.pack_into('<II',slot,8,0x20<<25,NOP)
        self.assertEqual(deps.analyze_image(bytes(slot))['status'],'unsupported')
        for raw in [b'',bytes(16383),bytes(16385)]:
            with self.assertRaises(ValueError):deps.analyze_image(raw)

    def test_straight_line_live_inputs_against_independent_execution_perturbations(self):
        # Small finite ADD-only programs use exact integer-valued floats. This
        # checks the live-input algorithm, not PS2 arithmetic or pipeline timing.
        rng=random.Random(5900)
        for _ in range(12):
            words=[add(rng.randrange(1,6),rng.randrange(0,6),rng.randrange(0,6),rng.randrange(1,16)) for _ in range(8)]
            report=entry0(deps.analyze_image(image(words)))
            inputs=vf_inputs(report)
            initial=[[float(r*4+c+1) for c in range(4)] for r in range(6)]
            initial[0]=[0.,0.,0.,1.]
            def execute(values):
                values=copy.deepcopy(values);written=set()
                for w in words:
                    fd,fs,ft=(w>>6)&31,(w>>11)&31,(w>>16)&31
                    before=copy.deepcopy(values)
                    for c in range(4):
                        if (w>>21)&(8>>c):
                            values[fd][c]=before[fs][c]+before[ft][c];written.add((fd,c))
                return {(r,c):values[r][c] for r,c in written}
            expected=execute(initial)
            for reg in range(1,6):
                for lane in range(4):
                    if (reg,lane) in inputs:continue
                    changed=copy.deepcopy(initial);changed[reg][lane]+=17
                    self.assertEqual(execute(changed),expected)
        # Negative control: treating a real input as independent changes output.
        words=[add(3,1,2)]
        before=execute(initial);changed=copy.deepcopy(initial);changed[1][0]+=17
        self.assertNotEqual(execute(changed),before)
        simple=image(words)
        self.assertIn((1,0),vf_inputs(entry0(deps.analyze_image(simple))))

    def test_private_outputs_are_new_bounded_and_source_preserving(self):
        with tempfile.TemporaryDirectory() as tmp:
            private=Path(tmp);capture=private/'capture';capture.mkdir()
            source=capture/'vu1_example.bin';source.write_bytes(image([add(3,1,2)]));before=source.read_bytes()
            first=deps.analyze_capture(private,capture,'first')
            out=private/'analysis/vu-dependencies/first/report.json'
            self.assertEqual(json.loads(out.read_text()),first)
            self.assertEqual(out.parent.stat().st_mode&0o777,0o700)
            self.assertEqual(source.read_bytes(),before)
            with self.assertRaises(FileExistsError):deps.analyze_capture(private,capture,'first')
            with self.assertRaises(ValueError):deps.analyze_capture(private,capture,'../escape')
            source.unlink();source.symlink_to(private/'other.bin')
            (private/'other.bin').write_bytes(before)
            with self.assertRaises(ValueError):deps.capture_images(private,capture)
            with self.assertRaises(ValueError):deps.capture_images(private,private.parent)
        check_target('cpp','analyze-vu-dependencies','Linux')
        check_target('macos','analyze-vu-dependencies','Darwin')

    def test_source_model_has_pins_hashes_and_no_runtime_activation(self):
        record=deps.implementation()
        self.assertEqual(record['revision'],deps.SOURCE_PIN)
        self.assertTrue(all(len(v)==64 for v in record['hashes'].values()))
        self.assertFalse(record['runtime_optimization_eligible'])

    def test_shared_cli_creates_private_report_without_native_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            private=Path(temp)/'private';capture=private/'capture';capture.mkdir(parents=True)
            (capture/'vu1_synthetic.bin').write_bytes(image([add(3,1,2)]))
            profile=Path(temp)/'synthetic.json'
            profile.write_text(json.dumps(dict(id='synthetic',private_dir=str(private),private_dir_absolute_opt_in=True)))
            command=[sys.executable,str(deps.ROOT/'run.py'),'--profile',str(profile),
                     'analyze-vu-dependencies',str(capture),'--name','cli']
            ran=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertEqual(ran.returncode,0,ran.stderr)
            record=json.loads((private/'analysis/vu-dependencies/cli/report.json').read_text())
            self.assertIn('vu1_synthetic.bin',record['images'])
            self.assertFalse(record['runtime_optimization_eligible'])
            self.assertFalse((private/'native-build').exists())
