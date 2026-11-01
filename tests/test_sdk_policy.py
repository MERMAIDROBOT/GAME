from pathlib import Path
import shutil,subprocess,tempfile,unittest
import native
ROOT=Path(__file__).resolve().parents[1]
class SdkPolicy(unittest.TestCase):
 def test_policy_is_carried_into_the_actual_recompiler_patch(self):
  patch=(ROOT/'patches/sdk-audio-policy.patch').read_text();header=(ROOT/'runtime/abi/sdk_policy.h').read_text()
  self.assertIn('patches/sdk-audio-policy.patch',native.RUNTIME_PATCHES)
  self.assertIn('ps2_port::requires_compiled_sdk_body(function.name)',patch)
  self.assertIn(''.join('+'+line+'\n' for line in header.splitlines()),patch)
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_compiled_selector_preserves_stateful_audio_bodies_without_disabling_other_stubs(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'check.cpp').write_text('''#include "sdk_policy.h"
#include <cassert>
int main() {
 assert(ps2_port::requires_compiled_sdk_body("sceSdRemote"));
 assert(ps2_port::requires_compiled_sdk_body("sceSdRemoteInit"));
 assert(!ps2_port::requires_compiled_sdk_body("scePrintf"));
 assert(!ps2_port::requires_compiled_sdk_body("sceSdRemoteUnrelated"));
 assert(!ps2_port::requires_compiled_sdk_body(""));
}
''')
   subprocess.run(['clang++','-std=c++17','-I',str(ROOT/'runtime/abi'),str(p/'check.cpp'),'-o',str(p/'check')],check=True,capture_output=True)
   subprocess.run([str(p/'check')],check=True,capture_output=True)
