from pathlib import Path
import subprocess,sys,tempfile,unittest
from compute_slot import slot,exclusive
class ComputeSlot(unittest.TestCase):
 def test_second_process_fails_while_owner_holds_then_succeeds_after_release(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'lock';script='from compute_slot import slot\nimport sys\nwith slot("child",path=sys.argv[1]): pass\n'
   with slot('parent',path=path):
    child=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True)
    self.assertNotEqual(child.returncode,0);self.assertIn('owns the compute slot',child.stderr)
   child=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True);self.assertEqual(child.returncode,0,child.stderr)
 def test_plan_only_operations_do_not_request_the_lock(self):
  from unittest.mock import patch
  @exclusive('fixture',when='execute')
  def operation(*,execute=False):return 7
  with patch('compute_slot.slot',side_effect=AssertionError('unexpected lock')):self.assertEqual(operation(),7)
 def test_source_symlink_lock_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   target=Path(d)/'original';target.write_text('keep');link=Path(d)/'link';link.symlink_to(target)
   with self.assertRaises((OSError,ValueError)):
    with slot('test',path=link):pass
   self.assertEqual(target.read_text(),'keep')

class ForeignComputeGuard(unittest.TestCase):
 def test_macos_capitalization_and_source_built_runner_names_are_not_missed(self):
  from compute_slot import conflicting_processes
  names=['/Applications/Blender.app/Contents/MacOS/Blender','/tmp/kelly_source_runner','/tmp/kelly_runtime_checks','/tmp/ps2EntryRunner','/tmp/blender','/tmp/CLANG++','/tmp/ps2_analyzer','/tmp/ps2_recomp','/tmp/ViewportProbe','/tmp/GameWindowRecorder','/usr/bin/swift-frontend','/usr/bin/swiftc']
  self.assertEqual(conflicting_processes('\n'.join(names)),names)
  self.assertEqual(conflicting_processes('/tmp/blender-assets/helper\n/Applications/Safari'),[])
 def test_active_process_refuses_operation_and_idle_listing_passes(self):
  from unittest.mock import patch
  from compute_slot import assert_idle
  with patch('compute_slot.subprocess.check_output',return_value='/Applications/Blender.app/Contents/MacOS/Blender\n'):
   with self.assertRaisesRegex(RuntimeError,'occupied'):assert_idle()
  with patch('compute_slot.subprocess.check_output',return_value='/usr/bin/login\n'):assert_idle()
