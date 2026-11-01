from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class RendererCarryforward(unittest.TestCase):
 def test_memory_authority_and_sustained_stall_decisions(self):self.run_fixture('renderer_carryforward_rules.cpp')
 def test_actual_owner_publishes_live_scale_without_pixel_fallback(self):self.run_fixture('hw_owner_live_scale.cpp')
 def run_fixture(self,name):
  with tempfile.TemporaryDirectory() as directory:
   compiler=shutil.which('clang++') or shutil.which('g++');output=Path(directory)/'check'
   build=subprocess.run([compiler,'-std=c++20','-O2',str(ROOT/'tests'/name),'-o',str(output)],capture_output=True,text=True,timeout=45)
   self.assertEqual(build.returncode,0,build.stderr)
   result=subprocess.run([str(output)],capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
