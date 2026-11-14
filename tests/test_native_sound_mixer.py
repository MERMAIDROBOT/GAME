import shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class NativeSoundMixerTests(unittest.TestCase):
    def test_gaussian_interpolation(self):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler: self.skipTest('no C++ compiler')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'probe'
            built=subprocess.run([compiler,'-std=c++20','-O2','-I'+str(ROOT/'runtime/native_sound/bozo/include'),
                                  str(ROOT/'tests/native_sound_mixer.cpp'),'-o',str(out)],capture_output=True,text=True,timeout=120)
            self.assertEqual(built.returncode,0,built.stderr)
            run=subprocess.run([str(out)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)

if __name__=='__main__': unittest.main()
