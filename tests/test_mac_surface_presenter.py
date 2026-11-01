from pathlib import Path
import platform,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(platform.system()=='Darwin' and shutil.which('clang++'),'macOS CGL/IOSurface required')
class MacSurfacePresenter(unittest.TestCase):
 def test_real_surface_gpu_copy_module_lifetime_and_context_recreation(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);producer=root/'producer.dylib';exe=root/'check'
   common=['clang++','-std=c++17','-Wno-deprecated-declarations','-framework','IOSurface','-framework','CoreFoundation','-framework','OpenGL']
   for args in [common+['-dynamiclib',str(ROOT/'tests/mac_surface_producer.cpp'),'-o',str(producer)],common+['-I'+str(ROOT/'runtime/presentation'),str(ROOT/'tests/mac_surface_presenter.cpp'),'-o',str(exe)]]:
    result=subprocess.run(args,capture_output=True,text=True,timeout=30);self.assertEqual(result.returncode,0,result.stderr)
   result=subprocess.run([str(exe),str(producer)],capture_output=True,text=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
