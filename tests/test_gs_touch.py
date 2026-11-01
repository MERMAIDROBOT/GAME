from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(shutil.which('clang++'),'Clang required')
class GsTouch(unittest.TestCase):
 def test_exact_boundary_and_optout(self):
  with tempfile.TemporaryDirectory() as t:
   g=ROOT/'runtime/gpu/host-model';out=Path(t)/'touch'
   args=['clang++','-std=c++17','-O1','-pthread','-I'+str(g/'include'),str(ROOT/'tests/gs_touch.cpp')]+[str(g/'src'/s) for s in ['gs.cpp','gs_draw.cpp','gs_memory.cpp']]+['-o',str(out)]
   p=subprocess.run(args,capture_output=True,text=True,timeout=90);self.assertEqual(p.returncode,0,p.stderr)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2HOST_'))}
   for extra,e in [([],env),(['off'],dict(env,PS2_PORT_GS_TIGHT_TOUCH='0'))]:
    p=subprocess.run([str(out),*extra],capture_output=True,text=True,env=e,timeout=20)
    self.assertEqual(p.returncode,0,p.stdout+p.stderr)
