from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
import tool_state

class ToolState(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  self.source=self.p/'source';self.source.mkdir();(self.source/'compiler.cpp').write_text('int compiler;')
  self.root=self.p/'engine';(self.root/'patches').mkdir(parents=True);(self.root/'patches/fix.patch').write_text('policy');(self.root/'toolchain.lock.json').write_text('{}')
  self.build=self.p/'build'
  for name in ['ps2xAnalyzer/ps2_analyzer','ps2xRecomp/ps2_recomp']:
   p=self.build/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
  self.lab=SimpleNamespace(ROOT=self.root,SOURCE=self.source,BUILD=self.build,LOCK=dict(ps2recomp=dict(revision='abc'),resolved_dependencies={}))
  self.patch=patch('native.RUNTIME_PATCHES',('patches/fix.patch',));self.patch.start();self.addCleanup(self.patch.stop)
  self.git=patch('tool_state.subprocess.check_output',return_value='abc\n');self.git.start();self.addCleanup(self.git.stop)
 def test_source_policy_and_binary_changes_all_invalidate_compiler_receipt(self):
  self.assertFalse(tool_state.matches(self.lab));tool_state.record(self.lab,tool_state.inputs(self.lab));self.assertTrue(tool_state.matches(self.lab))
  p=self.root/'patches/fix.patch';p.write_text('new policy');self.assertFalse(tool_state.matches(self.lab))
  tool_state.record(self.lab,tool_state.inputs(self.lab));(self.source/'compiler.cpp').write_text('changed source');self.assertFalse(tool_state.matches(self.lab))
  tool_state.record(self.lab,tool_state.inputs(self.lab));(self.build/'ps2xRecomp/ps2_recomp').write_bytes(b'changed binary');self.assertFalse(tool_state.matches(self.lab))
 def test_refresh_is_automatic_only_when_state_differs(self):
  with patch('tool_state.refresh') as refresh:tool_state.ensure(self.lab);refresh.assert_called_once_with(self.lab)
  tool_state.record(self.lab,tool_state.inputs(self.lab))
  with patch('tool_state.refresh') as refresh:tool_state.ensure(self.lab);refresh.assert_not_called()
 def test_source_changed_during_build_cannot_be_certified(self):
  expected=tool_state.inputs(self.lab);(self.source/'compiler.cpp').write_text('changed during build')
  with self.assertRaisesRegex(RuntimeError,'changed during build'):tool_state.record(self.lab,expected)
 def test_missing_locked_dependency_does_not_start_a_download(self):
  self.lab.LOCK['resolved_dependencies']={'missing':'abc'}
  with self.assertRaisesRegex(RuntimeError,'dependency missing'):tool_state.inputs(self.lab)
 def test_source_revision_is_not_replaced_by_last_dependency_revision(self):
  self.lab.LOCK['resolved_dependencies']={'dependency':'dep-revision'}
  dependency=self.build/'_deps/dependency-src';dependency.mkdir(parents=True);(dependency/'source.c').write_text('int dependency;')
  with patch.object(tool_state.subprocess,'check_output',side_effect=['abc\n','dep-revision\n']):
   inputs=tool_state.inputs(self.lab)
  self.assertEqual(inputs['source_revision'],'abc')
  self.assertEqual(inputs['dependencies']['dependency']['revision'],'dep-revision')
 def test_private_processing_checks_compiler_state_before_executing_the_tool(self):
  import pipeline
  from types import SimpleNamespace
  log=self.p/'tool.log'
  with patch.object(pipeline,'SOURCE',self.source),patch.object(pipeline,'BUILD',self.build),patch.object(pipeline,'LOCK',self.lab.LOCK),patch('tool_state.ensure') as ensure,patch.object(pipeline.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
   pipeline.run([self.build/'ps2xRecomp/ps2_recomp','private.toml'],log,offline=True)
   ensure.assert_called_once_with(pipeline)
  with patch('tool_state.ensure') as ensure:
   with self.assertRaisesRegex(RuntimeError,'Unrecognized'):pipeline.run(['/tmp/unknown-tool'],log,offline=True)
   ensure.assert_not_called()

 def test_wrong_source_revision_and_source_symlink_are_rejected(self):
  self.lab.LOCK['ps2recomp']['revision']='other'
  with self.assertRaisesRegex(ValueError,'revision differs'):tool_state.inputs(self.lab)
  self.lab.LOCK['ps2recomp']['revision']='abc';(self.source/'escape.h').symlink_to(self.source/'compiler.cpp')
  with self.assertRaisesRegex(ValueError,'symlink'):tool_state.inputs(self.lab)
 def test_only_verified_filesystem_metadata_is_excluded_from_source_pin(self):
  pin=tool_state.tree_pin(self.source)
  sidecar=self.source/'._compiler.cpp';sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  self.assertEqual(tool_state.tree_pin(self.source),pin)
  sidecar.write_text('int actual_source;')
  self.assertNotEqual(tool_state.tree_pin(self.source),pin)
