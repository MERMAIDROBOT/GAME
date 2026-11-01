import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import port_checks as pc


class PortChecks(unittest.TestCase):
    def app(self,tmp):
        root=Path(tmp);app=root/'Synthetic.app';r=app/'Contents/Resources';r.mkdir(parents=True)
        disc=root/'disc';disc.mkdir();(disc/'BOOT.ELF').write_bytes(b'synthetic')
        config=dict(DiscDirectory=str(disc),Executable=str(disc/'BOOT.ELF'),Environment={})
        (r/'RunConfiguration.plist').write_bytes(plistlib.dumps(config))
        lib=app/'Contents/Frameworks/libclaude-hwgs.dylib';lib.parent.mkdir();lib.write_bytes(b'fixture renderer')
        return app,disc,config

    def test_real_directories_pass_and_symlink_streaming_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,disc,config=self.app(tmp)
            self.assertEqual(pc.inspect_app(app),[])
            target=Path(tmp)/'real-audio';target.mkdir();(disc/'AUDIO').symlink_to(target,target_is_directory=True)
            self.assertIn('streaming_directory_symlinks',[f['code'] for f in pc.inspect_app(app)])

    def test_log_counters_never_certify_gameplay_or_unique_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log';log.write_text('[residency-cache] hits=95 misses=5\nfields=120 mean_ms=8 peak_ms=120\n0 missing frames, 0 underruns, 0 dropped frames\nload_failures=2\n')
            r=pc.audit(log=log)
            self.assertFalse(r['gameplay_verified']);self.assertFalse(r['structural_checks_passed'])
            self.assertEqual(r['metrics']['residency_query_avoidance'],.95)
            self.assertEqual(r['metrics']['field_peak_ms'],120)

    def test_hash_receipt_catches_wrong_renderer_and_missing_renderer_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,_,_=self.app(tmp);receipt=Path(tmp)/'stage.json'
            receipt.write_text(json.dumps({'files':{'Frameworks/libclaude-hwgs.dylib':'0'*64}}))
            self.assertFalse(pc.audit(app=app,receipt=receipt)['structural_checks_passed'])
            receipt.write_text(json.dumps({'files':{'Resources/RunConfiguration.plist':pc.sha(app/'Contents/Resources/RunConfiguration.plist')}}))
            self.assertIn('renderer_receipt_missing',[f['code'] for f in pc.audit(app=app,receipt=receipt)['findings']])

    def test_receipt_escape_and_stale_acceptance_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,_,_=self.app(tmp);receipt=Path(tmp)/'stage.json'
            receipt.write_text(json.dumps({'files':{'../../outside':'0'*64}}))
            with self.assertRaises(ValueError):pc.audit(app=app,receipt=receipt)
            receipt.write_text(json.dumps({'files':{'Frameworks/libclaude-hwgs.dylib':pc.sha(app/'Contents/Frameworks/libclaude-hwgs.dylib')}}))
            acceptance=Path(tmp)/'acceptance.json';acceptance.write_text(json.dumps({'receipt_sha256':'wrong'}))
            with self.assertRaises(ValueError):pc.audit(app=app,receipt=receipt,acceptance=acceptance)

    def test_diagnostic_settings_and_generated_branch_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,_,config=self.app(tmp);config['Environment']['PS2_PORT_PRIVATE_WINDOW_CONTROL']='1'
            (app/'Contents/Resources/RunConfiguration.plist').write_bytes(plistlib.dumps(config))
            src=Path(tmp)/'generated';src.mkdir();(src/'a.cpp').write_text('// 0x1000: 0x1d000001 bgtz\nif(GPR_S32(ctx,8)>0){}\n')
            r=pc.audit(app=app,generated=src)
            self.assertEqual({f['code'] for f in r['findings']},{'diagnostic_release_setting','ee_branch_width'})

    def test_declared_source_renderer_is_inventoried_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,_,config=self.app(tmp)
            (app/'Contents/Frameworks/libclaude-hwgs.dylib').unlink()
            plugin=app/'Contents/Frameworks/libnative.dylib';plugin.write_bytes(b'source-built fixture')
            config['FrameworkEnvironment']={'PS2_PORT_PRIVATE_GS_PLUGIN':'libnative.dylib'}
            cp=app/'Contents/Resources/RunConfiguration.plist';cp.write_bytes(plistlib.dumps(config))
            self.assertEqual(pc.inspect_app(app),[])
            receipt=Path(tmp)/'receipt.json';receipt.write_text(json.dumps({'files':{'Resources/RunConfiguration.plist':pc.sha(cp)}}))
            self.assertIn('renderer_receipt_missing',[f['code'] for f in pc.audit(app=app,receipt=receipt)['findings']])
            plugin.unlink()
            self.assertIn('renderer_not_inventoried',[f['code'] for f in pc.inspect_app(app) if f['severity']=='error'])
            config['FrameworkEnvironment']['PS2_PORT_PRIVATE_GS_PLUGIN']='../../outside.dylib';cp.write_bytes(plistlib.dumps(config))
            self.assertIn('renderer_binding_invalid',[f['code'] for f in pc.inspect_app(app)])

    def test_blocked_rpc_prevents_qualification_even_with_clean_frame_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log'
            log.write_text('[edge-trial timing] fields=120 mean_ms=0.0 peak_ms=0.0\n'
                           '[ps2xIOP:warning] [IOP] RPC server 0x1234 still blocked after 73728000 cycles\n'*2)
            result=pc.audit(log=log)
            self.assertFalse(result['structural_checks_passed'])
            self.assertFalse(result['gameplay_verified'])
            issue=next(f for f in result['findings'] if f['code']=='iop_rpc_blocked')
            self.assertEqual(issue['servers'],['0x1234'])
            self.assertEqual(issue['occurrences'],2)
            self.assertEqual(issue['maximum_wait_cycles'],73728000)

    def test_failed_iop_allocation_blocks_qualification_before_level_stalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log';log.write_text('[port-lab alloc] mode=0 bytes=1146880 fixed=0 result=0 free=831104\n')
            result=pc.audit(log=log);self.assertFalse(result['structural_checks_passed'])
            issue=next(f for f in result['findings'] if f['code']=='iop_allocation_failed')
            self.assertEqual(issue['requests'],[dict(mode=0,requested_bytes=1146880,free_bytes=831104)])

    def test_declared_sector_image_is_required_and_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,disc,config=self.app(tmp)
            cp=app/'Contents/Resources/RunConfiguration.plist'
            image=Path(tmp)/'disc.iso';image.write_bytes(b'synthetic sectors')
            for value in ('relative.iso', str(disc), str(image.with_name('missing.iso')), 12):
                config['CdImage']=value;cp.write_bytes(plistlib.dumps(config))
                self.assertIn('disc_image_missing',[f['code'] for f in pc.inspect_app(app)])
            config['CdImage']=str(image);cp.write_bytes(plistlib.dumps(config))
            self.assertEqual(pc.inspect_app(app),[])
            link=image.with_name('linked.iso');link.symlink_to(image)
            config['CdImage']=str(link);cp.write_bytes(plistlib.dumps(config))
            self.assertIn('disc_image_missing',[f['code'] for f in pc.inspect_app(app)])

    def test_unimplemented_sdk_and_missing_targets_block_clean_counter_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log'
            log.write_text('0 missing frames, 0 underruns, 0 dropped frames\n'
                'Warning: Unimplemented PS2 stub called. name=sceDmaCallback PC=0x1000\n'
                'Unimplemented PS2 stub called: sceDmaCallback\n'
                '[guest-branch:missing-target] JAL target=0x1234 caller=0x1000\n'
                '[ee] no translated code at 0x00005678\n')
            result=pc.audit(log=log)
            self.assertFalse(result['structural_checks_passed'])
            issues={x['code']:x for x in result['findings']}
            self.assertEqual(issues['sdk_handler_unimplemented']['symbols'],['sceDmaCallback'])
            self.assertEqual(issues['guest_target_missing']['addresses'],['00001234','00005678'])
            self.assertFalse(result['gameplay_verified'])

    def test_media_decode_errors_block_clean_counter_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log'
            log.write_text('0 missing frames, 0 underruns, 0 dropped frames\n'
                '[mpeg2video @ 0x1601fe7d0] Invalid mb type in P-frame at 4 20\n'
                '[mpeg2video @ 0x1601fe7d0] ac-tex damaged at 18 21\n'
                '[mpeg2video @ 0x1601fe7d0] mb incr damaged\n'
                '[mpeg2video @ 0x1601fe7d0] Warning MVs not available\n')
            result=pc.audit(log=log)
            issue=next(f for f in result['findings'] if f['code']=='media_decode_error')
            self.assertEqual(issue['observations'],3)
            self.assertEqual(result['metrics']['media_decode_error_count'],3)
            self.assertFalse(result['structural_checks_passed'])
            self.assertFalse(result['gameplay_verified'])

    def test_arbitrary_game_error_text_is_not_a_decoder_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'run.log'
            log.write_text('invalid data test initialized\nError decoding tutorial instruction\n'
                '[mpeg2video @ 0x1234] decoder initialized\n')
            self.assertNotIn('media_decode_error',[f['code'] for f in pc.audit(log=log)['findings']])

    def accepted_fixture(self, tmp, scenarios):
        app,_,_=self.app(tmp)
        (app/'Contents/MacOS').mkdir()
        (app/'Contents/MacOS/runner').write_bytes(b'synthetic runner')
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'runner'}))
        files={str(p.relative_to(app/'Contents')):pc.sha(p) for p in (app/'Contents').rglob('*') if p.is_file()}
        receipt=Path(tmp)/'receipt.json';receipt.write_text(json.dumps({'files':files}))
        evidence=Path(tmp)/'review.txt';evidence.write_text('Synthetic reviewer attestation; not game evidence.')
        acceptance=Path(tmp)/'acceptance.json'
        acceptance.write_text(json.dumps({'receipt_sha256':pc.sha(receipt),'scenarios':[
            {'id':name,'status':status,'evidence':str(evidence),'evidence_sha256':pc.sha(evidence)} for name,status in scenarios]}))
        return app,receipt,acceptance

    def test_clean_counters_and_partial_review_cannot_fill_missing_release_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,receipt,acceptance=self.accepted_fixture(tmp,[('navigation','passed')])
            r=pc.audit(app=app,receipt=receipt,acceptance=acceptance,required_scenarios=['navigation','motion','audio'])
            self.assertEqual(r['missing_scenarios'],['audio','motion'])
            self.assertFalse(r['structural_checks_passed'])
            self.assertFalse(r['declared_acceptance_complete'])
            r=pc.audit(app=app,receipt=receipt,required_scenarios=['audio'])
            self.assertEqual(r['missing_scenarios'],['audio'])

    def test_complete_pinned_declared_acceptance_keeps_machine_gameplay_claim_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,receipt,acceptance=self.accepted_fixture(tmp,[('navigation','passed'),('motion','passed')])
            r=pc.audit(app=app,receipt=receipt,acceptance=acceptance,required_scenarios=['navigation','motion'])
            self.assertTrue(r['declared_acceptance_complete'])
            self.assertFalse(r['gameplay_verified'])
            self.assertFalse(pc.audit(app=app,receipt=receipt,acceptance=acceptance)['declared_acceptance_complete'])
            (Path(tmp)/'review.txt').write_text('Evidence changed')
            with self.assertRaises(ValueError):pc.audit(app=app,receipt=receipt,acceptance=acceptance,required_scenarios=['motion'])

    def test_renderer_only_receipt_cannot_certify_unpinned_runner_or_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,receipt,acceptance=self.accepted_fixture(tmp,[('motion','passed')])
            data=json.loads(receipt.read_text());data['files']={k:v for k,v in data['files'].items() if k.startswith('Frameworks/')}
            receipt.write_text(json.dumps(data));a=json.loads(acceptance.read_text());a['receipt_sha256']=pc.sha(receipt);acceptance.write_text(json.dumps(a))
            r=pc.audit(app=app,receipt=receipt,acceptance=acceptance,required_scenarios=['motion'])
            self.assertFalse(r['declared_acceptance_complete'])
            missing={f['detail'] for f in r['findings'] if f['code']=='acceptance_artifact_unpinned'}
            self.assertEqual(missing,{'Info.plist','Resources/RunConfiguration.plist','MacOS/runner'})

    def test_ambiguous_duplicate_scenarios_and_invalid_requirements_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,receipt,acceptance=self.accepted_fixture(tmp,[('motion','passed'),('motion','failed')])
            with self.assertRaises(ValueError):pc.audit(app=app,receipt=receipt,acceptance=acceptance)
            for values in (['motion','motion'],[''],[None], 'motion', ['x'*129]):
                with self.assertRaises(ValueError):pc.audit(required_scenarios=values)

    def test_failed_scenario_prevents_release_even_when_required_motion_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,receipt,acceptance=self.accepted_fixture(tmp,[('motion','passed'),('audio','failed')])
            r=pc.audit(app=app,receipt=receipt,acceptance=acceptance,required_scenarios=['motion'])
            self.assertFalse(r['declared_acceptance_complete'])
            self.assertFalse(r['structural_checks_passed'])
