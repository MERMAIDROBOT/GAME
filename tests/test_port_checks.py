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
