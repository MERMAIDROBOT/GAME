"""The window must retain a producer lease until actual GPU completion."""
from pathlib import Path
import shutil, subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'), 'C++ compiler required')
class SurfaceConsumer(unittest.TestCase):
 def test_timeout_failure_and_shutdown_ownership(self):
  with tempfile.TemporaryDirectory() as directory:
   exe = Path(directory)/'check'
   result = subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++17', '-O2', '-I'+str(ROOT/'runtime/presentation'), str(ROOT/'tests/surface_consumer.cpp'), '-o', str(exe)], capture_output=True, text=True, timeout=30)
   self.assertEqual(result.returncode, 0, result.stderr)
   result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
   self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
