import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import conformance
from target_cli import check_target


class ConformanceTests(unittest.TestCase):
    def test_missing_rows_bad_hex_unknown_ops_and_denormals_cannot_pass(self):
        with self.assertRaises(ValueError):conformance.assess([],[])
        case=dict(op='add',a='00800000',b='007fffff',expected='00800000',upstream_known=2)
        for output in [[],['00800000','00800000'],['nothex']]:
            with self.assertRaises(ValueError):conformance.assess([case],output)
        self.assertFalse(conformance.assess([case],['00ffffff'],True)['accepted'])
        with self.assertRaises(ValueError):conformance.run('invented')

    def test_rounding_allowance_is_explicit_and_bounded(self):
        c=dict(op='add',a='3fffffff',b='3f800000',expected='403fffff',upstream_known=1)
        strict=conformance.assess([c],['40400000'])
        self.assertFalse(strict['accepted']);self.assertEqual(strict['skipped'],0)
        self.assertTrue(conformance.assess([c],['40400000'],True)['accepted'])
        self.assertFalse(conformance.assess([c],['40400001'],True)['accepted'])
        c['upstream_known']=0
        self.assertFalse(conformance.assess([c],['40400000'],True)['accepted'])

    def test_hardware_fixture_covers_all_six_ops_without_skips(self):
        data=conformance.load_vectors()
        self.assertEqual(len(data['cases']),174)
        self.assertEqual(set(c['op'] for c in data['cases']),set(conformance.OPS))
        self.assertFalse(data['provenance']['hardware_rerun'])
        self.assertTrue(any(c['upstream_known']==2 for c in data['cases']))

    def test_compiled_backends_detect_control_and_keep_deviations_visible(self):
        if not (shutil.which('clang++') or shutil.which('g++')):self.fail('C++20 compiler required for conformance')
        scalar=conformance.run('scalar');reference=conformance.run('reference');legacy=conformance.run('legacy')
        self.assertEqual(reference['exact'],174)
        self.assertTrue(reference['accepted'])
        self.assertEqual(scalar['failures'],0)
        self.assertGreater(scalar['exact'],legacy['exact'])
        self.assertGreater(legacy['failures'],0)
        self.assertGreater(scalar['known_rounding_differences'],0)
        self.assertFalse(scalar['accepted'])
        self.assertEqual(scalar['skipped'],0)
        self.assertTrue(conformance.assess(conformance.load_vectors()['cases'],
            [d['expected'] for d in conformance.load_vectors()['cases']],True)['accepted'])
        self.assertEqual(len(scalar['compiler_sha256']),64)
        self.assertIn('tools/ps2_scalar_fpu.h',scalar['source_hashes'])

    def test_cli_reaches_both_targets_and_does_not_overwrite_report(self):
        check_target('cpp','instruction-check','Linux')
        check_target('macos','instruction-check','Darwin')
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp)/'existing.json';report.write_text('keep')
            ran=subprocess.run([sys.executable,str(conformance.ROOT/'run.py'),'instruction-check','--report',str(report)],capture_output=True,text=True)
            self.assertNotEqual(ran.returncode,0)
            self.assertEqual(report.read_text(),'keep')
            self.assertIn('already exists',ran.stderr)

    def test_compile_failure_does_not_produce_accepted_result(self):
        fake=mock.Mock(returncode=1,stdout='version',stderr='deliberate compile failure')
        with mock.patch.object(conformance.subprocess,'run',return_value=fake):
            with self.assertRaisesRegex(RuntimeError,'compile failed'):
                conformance.run(compiler=sys.executable)

    def test_tool_timeout_is_an_explicit_failure(self):
        with mock.patch.object(conformance.subprocess,'run',side_effect=subprocess.TimeoutExpired('compiler',10)):
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                conformance.run(compiler=sys.executable)
