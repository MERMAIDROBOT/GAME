from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
import native,pipeline
from compute_slot import slot,assert_idle

class FrameEffects(unittest.TestCase):
 def test_disabled_game_movie_and_flipped_fxaa_boundaries(self):
  include=pipeline.WORK/'dependencies/raylib/src'
  if not (include/'raylib.h').exists():self.skipTest('Pinned raylib headers not provisioned')
  compiler=shutil.which('c++')
  if not compiler:self.skipTest('C++ compiler unavailable')
  with tempfile.TemporaryDirectory() as tmp,slot('frame-effects-synthetic'):
   assert_idle();binary=Path(tmp)/'frame-effects'
   subprocess.run([compiler,'-std=c++17','-I',str(include),str(pipeline.ROOT/'tests/frame_effects.cpp'),'-o',str(binary)],check=True,capture_output=True,text=True)
   env={k:v for k,v in os.environ.items() if not k.startswith('PS2_PORT_ENHANCE_')}
   for mode in ['identity','wide','movie','fxaa','hidpi']:
    with self.subTest(mode=mode):subprocess.run([str(binary),mode],check=True,capture_output=True,text=True,env=env)
 def test_public_helper_matches_ordered_patch(self):
  patch=(pipeline.ROOT/'patches/native-frame-effects.patch').read_text()
  self.assertIn('patches/native-frame-effects.patch',native.RUNTIME_PATCHES)
  self.assertIn('runtime/presentation/frame_effects.hpp',pipeline.SOURCE_FILES)
  content=''.join(line[1:]+'\n' for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
  self.assertIn((pipeline.ROOT/'runtime/presentation/frame_effects.hpp').read_text(),content)
