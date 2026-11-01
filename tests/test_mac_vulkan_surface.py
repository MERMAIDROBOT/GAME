from pathlib import Path
import os,platform,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
PLUGIN=Path(os.environ.get('PS2_PORT_NATIVE_COMPONENT_PLUGIN','/not-configured'))
DRIVER=Path(os.environ.get('PS2_PORT_NATIVE_COMPONENT_DRIVER','/not-configured'))
@unittest.skipUnless(platform.system()=='Darwin' and shutil.which('clang++') and PLUGIN.is_file() and DRIVER.is_file(),'Explicit source-built plugin and local MoltenVK required')
class MacVulkanSurface(unittest.TestCase):
 def test_actual_vulkan_producer_and_cgl_consumer_survive_teardown_and_resize(self):
  with tempfile.TemporaryDirectory() as directory:
   output=Path(directory)/'check'
   args=['clang++','-std=c++17','-Wno-deprecated-declarations','-I'+str(ROOT/'runtime/gpu/include'),'-I'+str(ROOT/'runtime/presentation'),str(ROOT/'tests/mac_vulkan_surface.cpp'),str(PLUGIN),'-Wl,-rpath,'+str(PLUGIN.parent),'-framework','IOSurface','-framework','CoreFoundation','-framework','OpenGL','-o',str(output)]
   result=subprocess.run(args,capture_output=True,text=True,timeout=30);self.assertEqual(result.returncode,0,result.stderr)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2HOST_','PS2X_')) and k not in {'DYLD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES','LD_PRELOAD','LD_LIBRARY_PATH'}}
   env.update(PS2_PORT_PRIVATE_MOLTENVK=str(DRIVER),PS2_PORT_HW_GS_ZERO_COPY='1',PS2_PORT_HW_GS_SUBPIXEL='1',PS2_PORT_HW_GS_SURFACE_METRICS='1')
   result=subprocess.run([str(output),str(PLUGIN)],env=env,capture_output=True,text=True,timeout=45)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   self.assertIn('renderer/device teardown passed',result.stdout)
   self.assertEqual(result.stderr.count('[native-surface acquired]'),2)
