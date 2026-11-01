from pathlib import Path
import hashlib,os,shutil,subprocess,tempfile,unittest
import runtime_header_replay as replay

class RuntimeHeaderReplay(unittest.TestCase):
 def test_private_macros_survive_and_unknown_scalar_overrides_fail(self):
  text='#pragma once\n#define PRIVATE_GAME_SETTING 19\n'+''.join(f'#define {name}(a'+('' if name=='FPU_SQRT_S' else ', b')+') '+old[0]+'\n' for name,(old,new) in replay.RULES.items())
  updated=replay.upgrade_macro_header(text,'scalar.h')
  self.assertIn('#define PRIVATE_GAME_SETTING 19',updated)
  self.assertEqual(replay.upgrade_macro_header(updated,'scalar.h'),updated)
  with self.assertRaisesRegex(ValueError,'Unrecognized'):replay.upgrade_macro_header(text.replace('((float)(a) / (float)(b))','unknown_division(a,b)'),'scalar.h')
 def test_known_helper_upgrade_and_real_macro_semantics(self):
  compiler=shutil.which('clang++') or shutil.which('g++')
  if not compiler:self.skipTest('C++20 compiler unavailable')
  current=(Path(replay.__file__).parent/'tools/ps2_scalar_fpu.h').read_text()
  old=current.replace('#include <cstdlib>\n','')
  start=old.index('// EE exponent-255 encodings');end=old.index('} // namespace ps2_port_fpu',start)
  old=old[:start]+old[end:]
  self.assertIn(hashlib.sha256(old.encode()).hexdigest(),replay.LEGACY_HELPERS)
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);helper=root/'ps2_port_scalar_fpu.h';helper.write_text(old)
   macro=root/'ps2_runtime_macros.h';macro.write_text('#pragma once\n'+''.join(f'#define {name}(a'+('' if name=='FPU_SQRT_S' else ', b')+') '+before[0]+'\n' for name,(before,new) in replay.RULES.items()))
   files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [helper,macro]}
   planned=replay.plan(root,files);self.assertEqual(planned['blockers'],[])
   for edit in planned['edits']:(root/edit['file']).write_text(edit['after'])
   source=root/'test.cpp';source.write_text('''#include "ps2_runtime_macros.h"
#include <bit>
int main(){float x=FPU_DIV_S(1.f,0.f);
if(std::bit_cast<unsigned>(x)!=0x7fffffffu)return 1;
if(!FPU_C_LT_S(1.f,x)||FPU_C_LE_S(x,1.f)||FPU_C_EQ_S(x,1.f))return 2;
if(FPU_MUL_S(2.f,3.f)!=6.f||FPU_SQRT_S(-16.f)!=4.f)return 3;return 0;}''')
   output=root/'check';built=subprocess.run([compiler,'-std=c++20','-O2',str(source),'-o',str(output)],capture_output=True,text=True,timeout=45);self.assertEqual(built.returncode,0,built.stderr)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2X_','PS2HOST_'))}
   ran=subprocess.run([str(output)],env=env,capture_output=True,text=True,timeout=10);self.assertEqual(ran.returncode,0,ran.stderr)
