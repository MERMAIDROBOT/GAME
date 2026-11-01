from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++'),'C++ compiler required')
class SurfaceOwner(unittest.TestCase):
 def test_actual_owner_opt_in_token_and_pixel_fallback(self):
  with tempfile.TemporaryDirectory() as directory:
   output=Path(directory)/'check'
   built=subprocess.run(['clang++','-std=c++20','-O2','-I'+str(ROOT/'runtime/gpu/native-plugin'),str(ROOT/'tests/surface_owner.cpp'),'-o',str(output)],capture_output=True,text=True,timeout=30)
   self.assertEqual(built.returncode,0,built.stderr)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2HOST_','PS2X_'))}
   for mode in ['default','invalid','surface','fallback','info-fallback']:
    with self.subTest(mode=mode):
     result=subprocess.run([str(output),mode],env=env,capture_output=True,text=True,timeout=10)
     self.assertEqual(result.returncode,0,result.stdout+result.stderr)
