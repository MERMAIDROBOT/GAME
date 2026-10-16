"""Scenario failure classification uses synthetic diagnostics only."""
from pathlib import Path
import sys
import unittest
import json
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios import analyze_log, VisualSteps, VisualPostconditions, completed_flip_timing
from scenarios import compare_scenarios, run_scenario, vu_verification_result
import pipeline

def beat(pc, cycle, dma=4, gif=2):
    return f'[port-lab] pc={hex(pc)} ra=0x2000 eeCycles={cycle} dma={dma} gif={gif} gs=0 threads=1\n'

class ScenarioTests(unittest.TestCase):
    def test_alternate_reference_preserves_threshold_ack_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            def ppm(value): return b'P6\n1 1\n255\n'+bytes([value]*3)
            for name,value in [('a',0),('b',100)]:
                (private/(name+'.ppm')).write_bytes(ppm(value))
            step={'reference':'a.ppm','reference_alternatives':['b.ppm'],
                  'region':[0,0,1,1],'max_mean_error':0,'min_delay_seconds':0}
            nav=VisualSteps(private,[step,step],private/'command')
            nav.update(ppm(50),1,set());self.assertEqual(nav.index,0)
            nav.update(ppm(100),2,set());self.assertEqual(nav.index,1)
            self.assertEqual(nav.events[0]['reference'],'b.ppm')
            self.assertEqual(nav.events[0]['reference_sha256'],nav.assets['b.ppm'])
            self.assertEqual(len(nav.assets),2)
            nav.update(ppm(0),3,set());self.assertEqual(nav.index,1)
            nav.update(ppm(0),4,{1});self.assertTrue(nav.completed)

    def test_alternate_reference_rejects_unsafe_malformed_and_unbounded_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);private=root/'private';private.mkdir()
            data=b'P6\n1 1\n255\n'+bytes(3)
            (private/'a.ppm').write_bytes(data);(root/'outside.ppm').write_bytes(data)
            (private/'bad.ppm').write_bytes(b'broken')
            (private/'large.ppm').write_bytes(b'P6\n2 1\n255\n'+bytes(6))
            step={'reference':'a.ppm','region':[0,0,1,1]}
            for alternatives in ('a.ppm',['a.ppm']*3,[None],['../outside.ppm'],['bad.ppm'],['large.ppm']):
                with self.subTest(alternatives=alternatives),self.assertRaises(ValueError):
                    VisualSteps(private,[dict(step,reference_alternatives=alternatives)],private/'command')

    def test_alternate_postcondition_requires_fresh_stable_acknowledged_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);a=b'P6\n1 1\n255\n'+bytes(3)
            b=b'P6\n1 1\n255\n'+bytes([100]*3)
            (private/'a.ppm').write_bytes(a);(private/'b.ppm').write_bytes(b)
            condition={'reference':'a.ppm','reference_alternatives':['b.ppm'],
                'region':[0,0,1,1],'max_mean_error':0,'after_sequence':1,'stable_seconds':1}
            checks=VisualPostconditions(private,[condition],1)
            checks.update(b,1,set(),1);self.assertFalse(checks.completed)
            checks.update(b,2,{1},1)
            checks.update(b,3,{1},1);self.assertFalse(checks.completed)
            checks.update(b,4,{1},2);self.assertFalse(checks.completed)
            checks.update(b,5,{1},3);self.assertTrue(checks.completed)
            self.assertEqual(checks.events[0]['reference'],'b.ppm')

    def test_aggregated_match_tolerates_small_edges_but_rejects_wrong_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            reference=b'P6\n2 2\n255\n'+bytes([100]*3+[120]*3+[100]*3+[120]*3)
            shifted=b'P6\n2 2\n255\n'+bytes([120]*3+[100]*3+[120]*3+[100]*3)
            wrong=b'P6\n2 2\n255\n'+bytes([220]*12)
            (private/'target.ppm').write_bytes(reference)
            nav=VisualSteps(private,[{'reference':'target.ppm','region':[0,0,2,2],
                'match_block_size':2,'max_mean_error':0,'max_fine_mean_error':25,
                'button_mask':8}],private/'command')
            nav.update(wrong,2,set());self.assertFalse(nav.completed)
            nav.update(shifted,3,set());self.assertTrue(nav.completed)
            self.assertEqual(nav.events[0]['fine_mean_error'],20)

    def test_aggregated_match_rejects_same_average_with_large_pixel_error(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            reference=b'P6\n2 2\n255\n'+bytes([0]*3+[255]*3+[0]*3+[255]*3)
            swapped=b'P6\n2 2\n255\n'+bytes([255]*3+[0]*3+[255]*3+[0]*3)
            (private/'target.ppm').write_bytes(reference)
            config={'reference':'target.ppm','region':[0,0,2,2],
                'match_block_size':2,'max_mean_error':0,'max_fine_mean_error':30}
            nav=VisualSteps(private,[config],private/'command')
            nav.update(swapped,2,set());self.assertFalse(nav.completed)
            for block in (0,9,1.5,True):
                with self.assertRaises(ValueError):
                    VisualSteps(private,[dict(config,match_block_size=block)],private/'command')

    def test_aggregated_postcondition_still_requires_ack_and_new_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);data=b'P6\n2 2\n255\n'+bytes([100]*12)
            (private/'target.ppm').write_bytes(data)
            checks=VisualPostconditions(private,[{'reference':'target.ppm','region':[0,0,2,2],
                'match_block_size':2,'after_sequence':1,'stable_seconds':1}],1)
            checks.update(data,1,set(),1);self.assertFalse(checks.completed)
            checks.update(data,2,{1},1)
            checks.update(data,3,{1},1);self.assertFalse(checks.completed)
            checks.update(data,4,{1},2)
            checks.update(data,5,{1},3);self.assertTrue(checks.completed)

    def test_postcondition_requires_ack_and_fresh_sustained_output(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);data=b'P6\n1 1\n255\n'+bytes([255,0,0])
            (private/'target.ppm').write_bytes(data)
            checks=VisualPostconditions(private,[{'name':'player-hud','reference':'target.ppm',
                'region':[0,0,1,1],'after_sequence':1,'stable_seconds':1,'min_frames':2}],1)
            self.assertFalse(checks.blocks_action(1))
            self.assertTrue(checks.blocks_action(2))
            checks.update(data,2,set(),1)
            self.assertFalse(checks.completed)
            checks.update(data,3,{1},1) # The existing image cannot establish a response.
            checks.update(data,4,{1},1)
            self.assertFalse(checks.completed)
            checks.update(data,4.1,{1},2)
            checks.update(data,4.5,{1},2) # Re-reading one capture isn't another frame.
            self.assertFalse(checks.completed)
            checks.update(data,5.2,{1},3)
            self.assertTrue(checks.completed)
            self.assertFalse(checks.blocks_action(2))
            self.assertEqual(checks.result()['status'],'passed')
            self.assertEqual(checks.events[0]['frames'],2)

    def test_postcondition_rejects_demo_stale_images_and_interrupted_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);data=b'P6\n1 1\n255\n'+bytes([255,0,0])
            demo=b'P6\n1 1\n255\n'+bytes([0,0,255]);(private/'target.ppm').write_bytes(data)
            checks=VisualPostconditions(private,[{'reference':'target.ppm','region':[0,0,1,1],
                'after_sequence':1,'stable_seconds':1,'timeout_seconds':5,'max_mean_error':0}],1)
            checks.update(data,0,{1},1)
            checks.update(data,1,{1},2)
            checks.update(demo,1.5,{1},3)
            checks.update(data,2,{1},4)
            self.assertFalse(checks.completed)
            with self.assertRaisesRegex(TimeoutError,'Postcondition'):
                checks.update(demo,5.1,{1},5)
            self.assertEqual(checks.result()['status'],'failed')

    def test_postcondition_missing_ack_is_not_a_pass_and_rejects_unbound_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);data=b'P6\n1 1\n255\n'+bytes(3)
            (private/'target.ppm').write_bytes(data)
            condition={'reference':'target.ppm','region':[0,0,1,1],'after_sequence':2}
            with self.assertRaisesRegex(ValueError,'issued action'):
                VisualPostconditions(private,[condition],1)
            checks=VisualPostconditions(private,[condition],2)
            checks.update(data,600,{1},1)
            self.assertEqual(checks.result()['status'],'incomplete')
            self.assertFalse(checks.completed)
            self.assertEqual(VisualPostconditions(private,[],0).result()['status'],'not_requested')

    def test_navigation_waits_for_input_ack_before_overwriting_command(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);data=b'P6\n1 1\n255\n'+bytes(3)
            (private/'target.ppm').write_bytes(data)
            steps=[{'reference':'target.ppm','region':[0,0,1,1],'min_delay_seconds':0}]*2
            command=private/'command';nav=VisualSteps(private,steps,command)
            nav.update(data,1,set())
            nav.update(data[:-1]+b'\x01',2,set())
            self.assertEqual(len(nav.events),1)
            nav.update(data[:-1]+b'\x01',2,{1})
            self.assertEqual(len(nav.events),2)

    def test_vector_verification_requires_evidence_and_preserves_failure(self):
        self.assertEqual(vu_verification_result('')['status'],'no_compiled_verification_observed')
        self.assertEqual(vu_verification_result('[vu1-verify] 16384 runs, 0 mismatches')['status'],'matched_observed_runs')
        result=vu_verification_result('[vu1-verify] mismatch #1 run 3\n[vu1-verify] 16384 runs, 0 mismatches')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['mismatches_lower_bound'],1)
        self.assertEqual(result['verified_runs_lower_bound'],16384)

    def test_completed_flip_timing_excludes_other_phases_and_window_refreshes(self):
        text='[port-lab completed-flip] ns=1000000000\n[port-lab pad-command] seq=2 mask=0\n'
        text+='[port-lab frame] 100x100 nonblack=10000\n'
        text+=''.join(f'[port-lab completed-flip] ns={n}\n' for n in (2000000000,2050000000,2100000000,2200000000))
        r=completed_flip_timing(text,2,0)
        self.assertEqual(r['samples'],4)
        self.assertAlmostEqual(r['completed_flips_per_second'],15)
        self.assertAlmostEqual(r['frame_interval_ms_p50'],50)
        self.assertAlmostEqual(r['frame_interval_ms_p95'],95)
        self.assertFalse(r['verified_display_fps'])
        self.assertEqual(completed_flip_timing(text,3,0)['samples'],0)
        self.assertEqual(completed_flip_timing(text,2,5)['samples'],0)

    def test_completed_flip_window_is_bounded_and_coverage_is_reported(self):
        text=''.join(f'[port-lab completed-flip] ns={n}\n' for n in (0,5000000000,10000000000,20000000000,21000000000))
        r=completed_flip_timing(text)
        self.assertEqual(r['samples'],3)
        self.assertEqual(r['seconds'],15)
        self.assertTrue(r['window_complete'])
        partial=completed_flip_timing(text.split('[port-lab completed-flip] ns=20000000000')[0])
        self.assertFalse(partial['window_complete'])

    def test_completed_flip_timing_rejects_nonmonotonic_evidence(self):
        with self.assertRaisesRegex(ValueError,'increase strictly'):
            completed_flip_timing('[port-lab completed-flip] ns=100\n[port-lab completed-flip] ns=100\n',None,0)

    def test_visual_navigation_matches_region_and_owns_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory); ref=private/'reference.ppm'
            data=b'P6\n2 1\n255\n'+bytes([255,0,0,0,255,0]);ref.write_bytes(data)
            command=private/'command.txt'
            nav=VisualSteps(private,[{'name':'target','reference':ref.name,'region':[1,0,1,1],
                                     'button_mask':8,'left_stick':[128,0],'hold_ms':1000}],command)
            nav.update(b'P6\n2 1\n255\n'+bytes([255,0,0,0,0,255]),2)
            self.assertFalse(command.exists())
            nav.update(data[:-1],2)
            self.assertFalse(command.exists())
            nav.update(data,2)
            self.assertEqual(command.read_text(),'1 8 1000 128 0\n')
            self.assertEqual(nav.index,1)
            self.assertEqual(nav.events[0]['mean_error'],0)
            self.assertEqual(nav.assets[ref.name],pipeline.digest(ref))

    def test_visual_navigation_rejects_private_escape_and_invalid_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);private=root/'private';private.mkdir()
            ref=root/'reference.ppm';ref.write_bytes(b'P6\n1 1\n255\n'+bytes(3))
            step={'reference':'../reference.ppm','region':[0,0,1,1]}
            with self.assertRaisesRegex(ValueError,'private game storage'):
                VisualSteps(private,[step],private/'command')
            (private/'reference.ppm').write_bytes(ref.read_bytes());step['reference']='reference.ppm'
            step['region']=[0,0,2,1]
            with self.assertRaisesRegex(ValueError,'outside'):
                VisualSteps(private,[step],private/'command')

    def test_post_actions_wait_for_navigation_and_use_its_completion_time(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);ref=private/'reference.ppm'
            data=b'P6\n1 1\n255\n'+bytes([255,0,0]);ref.write_bytes(data)
            command=private/'command'
            post=[{'name':'paddle','offset_seconds':2,'button_mask':16,'hold_ms':10000,
                   'left_stick':[128,0]},
                  {'name':'stand','offset_seconds':2.5,'button_mask':4112,'hold_ms':450}]
            nav=VisualSteps(private,[{'reference':ref.name,'region':[0,0,1,1],
                                      'timeout_seconds':30,'button_mask':8}],command,post)
            nav.update(b'P6\n1 1\n255\n'+bytes(3),10)
            self.assertFalse(command.exists())
            nav.update(data,11)
            nav.update(b'',12.9)
            self.assertEqual(command.read_text(),'1 8 250 128 128\n')
            self.assertFalse(nav.completed)
            nav.update(b'',13)
            self.assertEqual(command.read_text(),'2 16 10000 128 0\n')
            nav.update(b'',13.4)
            self.assertEqual(len(nav.events),2)
            nav.update(b'',13.5)
            self.assertEqual(command.read_text(),'3 4112 450 128 128\n')
            self.assertTrue(nav.completed)
            nav.update(data,99)
            self.assertEqual(len(nav.events),3)
            self.assertEqual(nav.events[-1]['source'],'post_navigation')

    def test_post_actions_reject_unbounded_or_ambiguous_input(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);ref=private/'reference.ppm'
            ref.write_bytes(b'P6\n1 1\n255\n'+bytes(3))
            steps=[{'reference':ref.name,'region':[0,0,1,1]}]
            for actions in ([{'offset_seconds':-1}], [{'offset_seconds':float('nan')}],
                            [{'offset_seconds':0,'hold_ms':10001}],
                            [{'offset_seconds':0,'left_stick':[256,0]}],
                            [{'offset_seconds':1},{'offset_seconds':1}]):
                with self.assertRaises(ValueError):
                    VisualSteps(private,steps,private/'command',actions)
            with self.assertRaises(ValueError):
                VisualSteps(private,[],private/'command',[{'offset_seconds':0}])

    def test_visual_navigation_timeout_does_not_issue_input(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);ref=private/'reference.ppm';ref.write_bytes(b'P6\n1 1\n255\n'+bytes(3))
            nav=VisualSteps(private,[{'reference':ref.name,'region':[0,0,1,1],'timeout_seconds':2}],private/'command')
            with self.assertRaises(TimeoutError): nav.update(ref.read_bytes(),3)
            self.assertEqual(nav.events,[])

    def test_advancing_cycles_do_not_hide_known_frozen_loop(self):
        report = analyze_log(''.join(beat(0x1234, n*1000) for n in range(10)), {'0x1234': 'synthetic loop'})
        self.assertEqual(report['known_stall'], 'synthetic loop')
        self.assertTrue(report['suspected_stall'])
        self.assertFalse(report['gameplay_verified'])

    def test_changing_transfers_do_not_report_same_pc_as_frozen(self):
        report = analyze_log(''.join(beat(0x1234, n*1000, dma=n) for n in range(10)))
        self.assertFalse(report['suspected_stall'])

    def test_missing_evidence_is_not_a_pass(self):
        report = analyze_log('unrelated log')
        self.assertIsNone(report['almost_black_fraction'])
        self.assertEqual(report['heartbeat_samples'], 0)
        self.assertFalse(report['gameplay_verified'])

    def test_visible_stalled_screen_and_black_frames_are_separate(self):
        text = '[port-lab frame] 100x100 nonblack=8000\n[port-lab frame] 100x100 nonblack=1\n'
        report = analyze_log(text + ''.join(beat(0x1234, n) for n in range(8)))
        self.assertEqual(report['almost_black_fraction'], .5)
        self.assertTrue(report['suspected_stall'])

    def test_comparison_rejects_different_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            for name, digest in [('baseline', 'a'), ('candidate', 'b')]:
                target = private / 'logs/scenarios' / name
                target.mkdir(parents=True)
                (target / 'report.json').write_text(json.dumps({'definition_sha256': digest}))
            with self.assertRaisesRegex(ValueError, 'different scenario definitions'):
                compare_scenarios(private, 'baseline', 'candidate', 'comparison')

    def test_bounded_runner_preserves_evidence_without_claiming_gameplay(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            (private / 'analysis').mkdir()
            (private / 'disc-root').mkdir()
            script = private / 'disc-root/synthetic.py'
            script.write_text('import os\nfrom pathlib import Path\n'
                              'Path(os.environ["PS2_PORT_FRAME"]).write_bytes(b"P6\\n1 1\\n255\\n\\xff\\x00\\x00")\n'
                              'print("[port-lab] pc=0x1234 ra=0x2000 eeCycles=4 dma=1 gif=2 gs=0 threads=1", flush=True)\n')
            binary = Path(sys.executable)
            (private / 'analysis/native-build-stage.json').write_text(json.dumps(
                {'binary': str(binary), 'sha256': pipeline.digest(binary)}))
            definition = private / 'analysis/scenario.json'
            definition.write_text(json.dumps({'duration_seconds': 1}))
            with patch.object(pipeline, 'SOURCE', private / 'synthetic-source'):
                report = run_scenario({'expected_executable': script.name}, private, definition, 'synthetic')
            self.assertEqual(report['outcome'], 'exit_0')
            self.assertFalse(report['gameplay_verified'])
            self.assertEqual(len(report['captures']), 1)
            self.assertEqual(report['heartbeat_samples'], 1)
            self.assertTrue((private / 'logs/scenarios/synthetic/report.json').exists())

    def test_all_inputs_acknowledged_but_wrong_result_fails_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);(private/'analysis').mkdir();(private/'disc-root').mkdir()
            data=b'P6\n1 1\n255\n'+bytes([255,0,0]);(private/'target.ppm').write_bytes(data)
            script=private/'disc-root/synthetic.py'
            script.write_text('import os,time\nfrom pathlib import Path\n'
                'frame=Path(os.environ["PS2_PORT_FRAME"])\n'
                'frame.write_bytes(b"P6\\n1 1\\n255\\n\\xff\\x00\\x00")\n'
                'command=Path(os.environ["PS2_PORT_PAD_COMMAND"])\n'
                'end=time.monotonic()+1\n'
                'while not command.exists() and time.monotonic()<end: time.sleep(.02)\n'
                'print("[port-lab pad-command] seq=1 mask=8",flush=True)\n'
                'frame.write_bytes(b"P6\\n1 1\\n255\\n\\x00\\x00\\xff")\n'
                'time.sleep(.6)\n')
            binary=Path(sys.executable)
            (private/'analysis/native-build-stage.json').write_text(json.dumps(
                {'binary':str(binary),'sha256':pipeline.digest(binary)}))
            definition=private/'analysis/scenario.json'
            definition.write_text(json.dumps({'duration_seconds':2,'visual_steps':[
                {'reference':'target.ppm','region':[0,0,1,1],'min_delay_seconds':0,'button_mask':8}],
                'visual_postconditions':[{'reference':'target.ppm','region':[0,0,1,1],
                    'after_sequence':1,'min_delay_seconds':0,'max_mean_error':0}]}))
            with patch.object(pipeline,'SOURCE',private/'synthetic-source'):
                report=run_scenario({'expected_executable':script.name},private,definition,'wrong-result')
            self.assertTrue(report['navigation_completed'])
            self.assertEqual(report['input_acknowledgements'],[1])
            self.assertEqual(report['status'],'postcondition_failed')
            self.assertFalse(report['gameplay_verified'])

    def test_comparison_rejects_timing_qualification_when_outcome_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            record={'definition_sha256':'same','status':'observed','navigation_completed':True,
                    'completed_flip_timing':{'window_complete':True}}
            for name in ('before','after'):
                target=private/'logs/scenarios'/name;target.mkdir(parents=True)
                (target/'report.json').write_text(json.dumps(record))
            report=compare_scenarios(private,'before','after','comparison')
            self.assertFalse(report['eligible_for_timing_comparison'])
            self.assertEqual(len(report['comparison_rejection_reasons']),2)

    def test_comparison_rejects_raw_recording_even_with_complete_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)
            record={'definition_sha256':'same','status':'observed','navigation_completed':True,
                    'postconditions':{'status':'passed'},
                    'completed_flip_timing':{'window_complete':True}}
            for name in ('before','after'):
                target=private/'logs/scenarios'/name;target.mkdir(parents=True)
                data=dict(record)
                if name=='after':data['raw_gs_capture']={'status':'complete','replayable':True}
                (target/'report.json').write_text(json.dumps(data))
            report=compare_scenarios(private,'before','after','comparison')
            self.assertFalse(report['eligible_for_timing_comparison'])
            self.assertEqual(report['comparison_rejection_reasons'],[
                'candidate: raw graphics recording invalidates performance comparison'])

    def test_graphics_history_treatment_is_recorded_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            (private / 'analysis').mkdir()
            (private / 'disc-root').mkdir()
            script = private / 'disc-root/synthetic.py'
            script.write_text('import os\nprint("trace=" + os.environ.get("PS2_PORT_GS_TRACE", "absent"), flush=True)\n'
                              'print("history=" + str("PS2_PORT_GS_HISTORY" in os.environ), flush=True)\n')
            binary = Path(sys.executable)
            (private / 'analysis/native-build-stage.json').write_text(json.dumps(
                {'binary': str(binary), 'sha256': pipeline.digest(binary)}))
            definition = private / 'analysis/scenario.json'
            definition.write_text(json.dumps({'duration_seconds': 1}))
            with patch.object(pipeline, 'SOURCE', private / 'synthetic-source'), patch.dict('os.environ', {'PS2_PORT_GS_TRACE':'1'}):
                for treatment in ('off', 'on'):
                    report = run_scenario({'expected_executable': script.name}, private, definition,
                                          'trace-' + treatment, graphics_history=treatment)
                    self.assertEqual(report['runtime_variant']['graphics_history'], treatment)
                    text = (private / 'logs/scenarios' / ('trace-' + treatment) / 'runner.log').read_text()
                    self.assertIn('trace=' + ('1' if treatment == 'on' else 'absent'), text)
                    self.assertIn('history=' + str(treatment == 'on'), text)
                definition.write_text(json.dumps({'duration_seconds': 1, 'environment': {'PS2_PORT_GS_TRACE':'1'}}))
                with self.assertRaisesRegex(ValueError, 'cannot override'):
                    run_scenario({'expected_executable':script.name}, private, definition,
                                 'conflict', graphics_history='off')

    def test_vector_capture_owns_paths_and_requires_capable_build(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory);(private/'analysis').mkdir();(private/'disc-root').mkdir()
            script=private/'disc-root/synthetic.py'
            script.write_text('import os\nprint("capture="+os.getenv("PS2X_VU1_CAPTURE","absent"))\n'
                              'print("recomp="+os.getenv("PS2X_VU1_RECOMP","absent"))\n'
                              'print("verify="+os.getenv("PS2X_VU1_VERIFY","absent"))\n')
            binary=Path(sys.executable);stage=private/'analysis/native-build-stage.json'
            record={'binary':str(binary),'sha256':pipeline.digest(binary)};stage.write_text(json.dumps(record))
            definition=private/'analysis/scenario.json'
            definition.write_text(json.dumps({'duration_seconds':1,'capture_vu_programs':True,'vu_execution':'verify'}))
            with patch.object(pipeline,'SOURCE',private/'synthetic-source'):
                with self.assertRaisesRegex(ValueError,'does not support'):
                    run_scenario({'expected_executable':script.name},private,definition,'unsupported')
                record['vu_execution_supported']=True;stage.write_text(json.dumps(record))
                with patch.dict('os.environ',{'PS2X_VU1_CAPTURE':'/unrequested/path','PS2X_VU1_VERIFY':'1'}):
                    report=run_scenario({'expected_executable':script.name},private,definition,'vector')
                capture=private/'logs/scenarios/vector/vu-code'
                text=(capture.parent/'runner.log').read_text()
                self.assertIn('capture='+str(capture.resolve()),text)
                self.assertIn('recomp=1',text);self.assertIn('verify=1',text)
                self.assertEqual(capture.stat().st_mode & 0o777,0o700)
                self.assertEqual(report['runtime_variant']['vu_execution'],'verify')
                definition.write_text(json.dumps({'duration_seconds':1}))
                with patch.dict('os.environ',{'PS2X_VU1_CAPTURE':'/unrequested/path','PS2X_VU1_VERIFY':'1'}):
                    run_scenario({'expected_executable':script.name},private,definition,'vector-default')
                text=(private/'logs/scenarios/vector-default/runner.log').read_text()
                self.assertIn('capture=absent',text);self.assertIn('verify=absent',text)

if __name__ == '__main__':
    unittest.main()
