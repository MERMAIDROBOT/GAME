from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
from compute_slot import slot,assert_idle
ROOT=Path(__file__).resolve().parents[1]

class StoragePaths(unittest.TestCase):
 def test_policy_is_present_in_actual_runtime_patch(self):
  import native
  self.assertIn('patches/native-save-root.patch',native.RUNTIME_PATCHES)
  header=(ROOT/'runtime/host_environment/storage_paths.hpp').read_text().removeprefix('#pragma once\n')
  patch=(ROOT/'patches/native-save-root.patch').read_text()
  self.assertIn(''.join('+'+line+'\n' for line in header.splitlines()),patch)
  for field in ('defaults','paths','normalized'):
   self.assertIn(f'{field}.mcRoot = ps2_port::memory_card_root({field}.elfDirectory);',patch)
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_compiled_policy_separates_saves_without_changing_legacy_fallback(self):
  with slot('synthetic-save-root-test'):
   assert_idle()
   with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp);src=p/'check.cpp';exe=p/'check'
    src.write_text('''#include "storage_paths.hpp"
#include <iostream>
int main() { try { std::cout << ps2_port::memory_card_root("/read-only/disc").string();return 0; }
catch (const std::runtime_error &) {return 7;} }
''')
    subprocess.run(['clang++','-std=c++17','-I',str(ROOT/'runtime/host_environment'),str(src),'-o',str(exe)],check=True,capture_output=True)
    base={k:v for k,v in os.environ.items() if not k.startswith('PS2_PORT_')}
    out=subprocess.run([str(exe)],env=base,text=True,capture_output=True,check=True)
    self.assertEqual(out.stdout,'/read-only/disc/mc0')
    saves=p/'persistent/mc0'
    out=subprocess.run([str(exe)],env=dict(base,PS2_PORT_SAVE_ROOT=str(saves)),text=True,capture_output=True,check=True)
    self.assertEqual(out.stdout,str(saves));self.assertFalse(saves.exists())
    for value in ('relative/mc0',''):
     out=subprocess.run([str(exe)],env=dict(base,PS2_PORT_SAVE_ROOT=value))
     self.assertEqual(out.returncode,7)
