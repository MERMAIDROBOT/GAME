from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class AlphaCutRule(unittest.TestCase):
 def test_texture_alpha_control_and_geometry_exclusions(self):
  with tempfile.TemporaryDirectory() as directory:
   output=Path(directory)/'check';compiler=shutil.which('clang++') or shutil.which('g++')
   build=subprocess.run([compiler,'-std=c++17',str(ROOT/'tests/alpha_cut_rule.cpp'),'-o',str(output)],capture_output=True,text=True,timeout=30)
   self.assertEqual(build.returncode,0,build.stderr)
   result=subprocess.run([str(output)],capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('7/7 passed',result.stdout)
