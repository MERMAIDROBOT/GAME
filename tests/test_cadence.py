import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cadence import analyze, analyze_capture
from target_cli import check_target


def capture(divisor=1, speed=1):
    return [dict(seconds=str(i/60), field=str(int(i*speed)), game_frame=str(i//2),
                 hash_valid='1', image_changed=str(int(i%divisor == 0))) for i in range(121)]


class CadenceTests(unittest.TestCase):
    def test_sixty_presents_thirty_image_changes_normal_speed(self):
        report = analyze(capture(divisor=2), 60)
        for key,value in [('presentation_hz',60),('image_change_hz',30),
                          ('simulation_update_hz',30),('emulated_speed_ratio',1)]:
            self.assertAlmostEqual(report[key], value)
        self.assertAlmostEqual(report['completed_image_hold_ms']['median'], 1000/30)
        self.assertFalse(report['gameplay_verified'])

    def test_double_speed_not_mistaken_for_smoothness(self):
        report = analyze(capture(speed=2), 60)
        self.assertEqual(report['emulated_speed_ratio'], 2)
        self.assertEqual(report['presentation_hz'], 60)

    def test_no_hashes_means_unknown_not_zero_image_rate(self):
        rows = capture()
        for row in rows:
            row.pop('hash_valid'); row.pop('image_changed'); row.pop('game_frame')
        report = analyze(rows)
        self.assertIsNone(report['image_change_hz'])
        self.assertIsNone(report['simulation_update_hz'])
        self.assertEqual(report['image_comparison_coverage'], 0)

    def test_invalid_hash_gap_does_not_bridge_hold_intervals(self):
        rows = capture(); rows[30]['hash_valid'] = '0'
        report = analyze(rows, 60)
        self.assertEqual(report['image_comparison_intervals'], 118)
        self.assertLess(report['completed_image_hold_ms']['maximum'], 17)

    def test_static_scene_can_repeat_all_images(self):
        rows = capture()
        for row in rows: row['image_changed'] = '0'
        report = analyze(rows, 60)
        self.assertEqual(report['image_change_hz'], 0)
        self.assertIsNone(report['completed_image_hold_ms'])

    def test_invalid_capture_rejected(self):
        for key,value in [('seconds','nan'),('seconds','-1'),('field','-1'),
                          ('field','0'),('hash_valid','yes'),('image_changed','')]:
            rows = capture(); rows[20][key] = value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): analyze(rows)
        for rows in [[], capture()[:1], [dict(seconds='0',field='1'),dict(seconds='0',field='2')]]:
            with self.assertRaises(ValueError): analyze(rows)
        for rate in [0,float('inf'),-60]:
            with self.assertRaises(ValueError): analyze(capture(),rate)

    def test_game_counter_gaps_and_resets_rejected(self):
        for value in ['', '0']:
            rows = capture(); rows[20]['game_frame'] = value
            with self.assertRaises(ValueError): analyze(rows)

    def test_private_receipt_and_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            private = Path(temp)/'private'; private.mkdir()
            source = private/'capture.csv'
            source.write_text('seconds,field,hash_valid,image_changed\n0,0,1,1\n1,60,1,1\n')
            # The core analyzer must not depend on Unix-only replay locking.
            with patch.dict(sys.modules, {'replay':None, 'fcntl':None}):
                result = analyze_capture({'id':'synthetic'},private,source.name,'a',60)
            report = private/'analysis/cadence/a/report.json'
            self.assertEqual(json.loads(report.read_text()),result)
            self.assertTrue(result['local_only'])
            with self.assertRaises(FileExistsError): analyze_capture({'id':'synthetic'},private,source.name,'a',60)
            outside = Path(temp)/'outside.csv'; outside.write_text(source.read_text())
            with self.assertRaises(ValueError): analyze_capture({'id':'synthetic'},private,'../outside.csv','b')
            source.write_text('seconds,seconds,field\n0,0,0\n1,1,60\n')
            with self.assertRaises(ValueError): analyze_capture({'id':'synthetic'},private,source.name,'b')
            self.assertFalse((private/'analysis/cadence/b').exists())

    def test_cross_platform_core_command(self):
        check_target('cpp','analyze-cadence',system='Linux')
        check_target('macos','analyze-cadence',system='Darwin')

    def test_cli_reads_private_csv_and_rejects_malformed_rows_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            private = Path(temp)/'private'; private.mkdir()
            profile = Path(temp)/'profile.json'
            profile.write_text(json.dumps(dict(id='synthetic', private_dir=str(private),
                                               private_dir_absolute_opt_in=True)))
            source = private/'capture.csv'
            source.write_text('seconds,field\n0,0\n1,60\n')
            root = Path(__file__).resolve().parents[1]
            env = {k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
            command = [sys.executable,str(root/'run.py'),'--profile',str(profile),
                       'analyze-cadence','capture.csv','--name','cli','--expected-field-hz','60']
            result = subprocess.run(command,capture_output=True,text=True,env=env,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['emulated_speed_ratio'],1)
            source.write_text('seconds,field\n0,0\n1\n')
            result = subprocess.run(command,capture_output=True,text=True,env=env,timeout=30)
            self.assertEqual(result.returncode,1)
            self.assertNotIn('Traceback',result.stderr)
            self.assertIn('counters',result.stderr)
