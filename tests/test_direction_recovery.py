"""Compiled remote direction policy; no game, GPU or device needed."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class DirectionRecovery(unittest.TestCase):
    def test_dropouts_and_release_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'policy'
            built=subprocess.run([shutil.which('clang++') or shutil.which('g++'),'-std=c++20','-O2',
                '-I'+str(ROOT/'runtime/host_input'),str(ROOT/'tests/direction_recovery.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
