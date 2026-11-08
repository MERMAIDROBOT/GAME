from pathlib import Path
import os,platform,re,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class AlphaProof(unittest.TestCase):

 def test_alpha_interval_proofs_preserve_every_possible_alpha_test_outcome(self):
  with tempfile.TemporaryDirectory() as directory:
   exe=Path(directory)/'alpha'
   subprocess.run([shutil.which('clang++') or shutil.which('g++'),'-std=c++17','-O2','-I'+str(ROOT/'runtime/gpu/src'),'-I'+str(ROOT/'runtime/gpu/include'),str(ROOT/'tests/alpha_proof.cpp'),'-o',str(exe)],check=True,timeout=30)
   subprocess.run([str(exe)],check=True,timeout=10)

 def test_ordered_alpha_coverage_proofs(self):
  with tempfile.TemporaryDirectory() as directory:
   exe=Path(directory)/'coverage'
   subprocess.run(['clang++','-std=c++17','-O2','-I'+str(ROOT/'runtime/gpu/src'),'-I'+str(ROOT/'runtime/gpu/include'),str(ROOT/'tests/ordered_coverage.cpp'),'-o',str(exe)],check=True,timeout=30)
   subprocess.run([str(exe)],check=True,timeout=10)

 @unittest.skipUnless(platform.system()=='Darwin' and Path(os.environ.get('PS2_PORT_NATIVE_COMPONENT_PLUGIN','/not-configured')).is_file() and Path(os.environ.get('PS2_PORT_NATIVE_COMPONENT_DRIVER','/not-configured')).is_file(),'Explicit source-built plugin and local driver required')
 def test_gpu_pixels_identical_with_alpha_proof_enabled(self):
  from compute_slot import slot,assert_idle
  with slot('alpha-proof-gpu-check'):
   assert_idle()
   plugin=Path(os.environ['PS2_PORT_NATIVE_COMPONENT_PLUGIN']).resolve()
   with tempfile.TemporaryDirectory() as directory:
    exe=Path(directory)/'gpu'
    subprocess.run(['clang++','-std=c++17','-O2','-I'+str(ROOT/'runtime/gpu/include'),str(ROOT/'tests/alpha_gpu_compare.cpp'),str(plugin),'-Wl,-rpath,'+str(plugin.parent),'-o',str(exe)],check=True,timeout=30)
    outputs=[];draws=[]
    for alpha,coverage in (('0','0'),('1','0'),('1','1')):
     env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2HOST_','PS2X_'))}
     env.update(PS2_PORT_PRIVATE_MOLTENVK=os.environ['PS2_PORT_NATIVE_COMPONENT_DRIVER'],PS2_PORT_HW_GS_ALPHA_PROOF=alpha,PS2_PORT_HW_GS_ALPHA_COVERAGE=coverage)
     run=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=90)
     self.assertEqual(run.returncode,0,run.stderr);outputs.append(run.stdout.strip())
     counters=next(line for line in run.stderr.splitlines() if '[alpha gpu counters]' in line)
     draws.append(int(re.search(r'(\d+) draws',counters).group(1)))
     print('alpha='+alpha+' coverage='+coverage+' '+counters+' '+run.stdout.strip(),flush=True)
    self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[0],outputs[2]);self.assertLess(draws[2],draws[1]);self.assertIn('512 overlapping',outputs[0])
