from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++'),'clang++ required')
class ScanoutQualification(unittest.TestCase):
 def test_observed_movie_and_invalid_neighbors(self):
  with tempfile.TemporaryDirectory() as tmp:
   exe=Path(tmp)/'qualification'
   subprocess.run(['clang++','-std=c++17',str(ROOT/'tests/scanout_qualification.cpp'),'-o',str(exe)],check=True,capture_output=True)
   ran=subprocess.run([str(exe)],capture_output=True,text=True)
   self.assertEqual(ran.returncode,0,ran.stderr)
