"""Execute actual GPU-worker scheduling against a deterministic renderer mock."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class GpuWorker(unittest.TestCase):
    def test_draw_order_upload_failure_eviction_and_residency_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'probe';cc=shutil.which('clang++') or shutil.which('g++')
            r=subprocess.run([cc,'-std=c++17','-O2','-pthread',
                '-I'+str(ROOT/'runtime/gpu/include'),str(ROOT/'runtime/gpu/src/threaded_renderer.cpp'),
                str(ROOT/'tests/gpu_residency.cpp'),'-o',str(out)],capture_output=True,text=True,timeout=60)
            self.assertEqual(r.returncode,0,r.stderr)
            env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2HOST_'))}
            run=subprocess.run([str(out)],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('1000 draws preserved',run.stdout)
