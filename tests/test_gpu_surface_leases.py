"""Compile actual presentation ownership and ABI declarations without a GPU/window."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class GpuSurfaceLeases(unittest.TestCase):
 def test_surfaces_outlive_renderer_reset_and_stale_releases_do_not_touch_new_frames(self):
  with tempfile.TemporaryDirectory() as directory:
   exe=Path(directory)/'check'
   result=subprocess.run([shutil.which('clang++') or shutil.which('g++'),'-std=c++17','-O2','-I'+str(ROOT/'runtime/gpu/src'),'-I'+str(ROOT/'runtime/gpu/include'),str(ROOT/'tests/gpu_surface_leases.cpp'),'-o',str(exe)],capture_output=True,text=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stderr)
   result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
