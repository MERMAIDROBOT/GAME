from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class HostEnvironment(unittest.TestCase):
 @unittest.skipUnless(shutil.which('clang'),'Clang required')
 def test_mutation_aware_cache_preserves_libc_environment_semantics(self):
  with tempfile.TemporaryDirectory() as directory:
   exe=Path(directory)/'check'
   args=['clang','-std=gnu11','-O2','-pthread',str(ROOT/'runtime/host_environment/env_cache.c'),str(ROOT/'tests/host_environment.c'),'-o',str(exe)]
   if os.uname().sysname=='Linux':args+=['-ldl']
   compiled=subprocess.run(args,capture_output=True,text=True,timeout=30);self.assertEqual(compiled.returncode,0,compiled.stderr)
   for arguments in ([],['mutable']):
    with self.subTest(arguments=arguments):
     result=subprocess.run([str(exe),*arguments],capture_output=True,text=True,timeout=10);self.assertEqual(result.returncode,0,result.stderr);self.assertIn('PASS:',result.stdout)
