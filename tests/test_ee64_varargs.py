from pathlib import Path
import hashlib,json,os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

def actual_cursor():
 text=(ROOT/'runtime/abi/Support.h').read_text()
 return text[text.index('    // Opt-out: PS2_PORT_EE64_VARARGS'):text.index('    class Ps2VaListCursor')]

class EE64Varargs(unittest.TestCase):
 def test_source_pin_and_registered_patch(self):
  origin=json.loads((ROOT/'runtime/abi/ORIGIN.json').read_text())
  self.assertEqual(hashlib.sha256((ROOT/'runtime/abi/Support.h').read_bytes()).hexdigest(),origin['candidate_sha256'])
  import native
  self.assertIn('patches/ee64-varargs.patch',native.RUNTIME_PATCHES)
  self.assertIn('m_ctx->r[4 + m_slotIndex]',actual_cursor())
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_actual_cursor_registers_mixed_arguments_boundary_and_optout(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'actual_cursor.h').write_text(actual_cursor())
   subprocess.run([shutil.which('clang++'),'-std=c++20','-O2','-I'+str(p),str(ROOT/'tests/ee64_varargs.cpp'),'-o',str(p/'check')],check=True,capture_output=True)
   env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2X_'))}
   subprocess.run([str(p/'check')],env=env,check=True,capture_output=True)
   subprocess.run([str(p/'check'),'legacy'],env=dict(env,PS2_PORT_EE64_VARARGS='0'),check=True,capture_output=True)
