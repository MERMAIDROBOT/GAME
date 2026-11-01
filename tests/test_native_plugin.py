from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path(os.environ.get('PS2_PORT_RUNTIME_SOURCE','/not-configured'))
class NativePlugin(unittest.TestCase):
 @unittest.skipUnless(shutil.which('clang++') and (RUNTIME/'include/runtime/gs/gs_backend.h').is_file(),'Matching public runtime source required')
 def test_actual_factory_preserves_worker_ownership_ingress_and_vram_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   output=Path(d)/'check';adapter=ROOT/'runtime/gpu/native-plugin'
   args=['clang++','-std=c++20','-O2','-pthread','-I'+str(adapter),'-I'+str(RUNTIME/'include'),str(adapter/'plugin.cpp'),str(RUNTIME/'src/lib/gs/ps2_gs_memory.cpp'),str(ROOT/'tests/native_plugin.cpp'),'-o',str(output)]
   built=subprocess.run(args,capture_output=True,text=True,timeout=60);self.assertEqual(built.returncode,0,built.stderr)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2X_','PS2HOST_'))}
   result=subprocess.run([str(output)],env=env,capture_output=True,text=True,timeout=15);self.assertEqual(result.returncode,0,result.stderr)
