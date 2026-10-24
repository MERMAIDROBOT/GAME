"""Actual shared scanout component: lifetime and zero-copy resolution."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class FrameOwnership(unittest.TestCase):
    def test_full_size_frame_retains_future_owner_without_payload_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'frame'
            built=subprocess.run([shutil.which('clang++') or shutil.which('g++'),'-std=c++17','-O2','-pthread',
                '-I'+str(ROOT/'runtime/presentation'),str(ROOT/'tests/frame_ownership.cpp'),'-o',str(exe)],capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            run=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
