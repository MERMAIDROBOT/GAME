from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
import offline_toolchain
import compute_slot


class OfflineToolchain(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name).resolve()
  real_slot=compute_slot.slot
  lock=patch.object(compute_slot,'slot',side_effect=lambda kind:real_slot(kind,path=self.p/'compute.lock'))
  lock.start();self.addCleanup(lock.stop)
  source=self.p/'source';source.mkdir();(source/'input.cpp').write_text('private working-tree edit')
  engine=self.p/'engine';engine.mkdir();(engine/'toolchain.lock.json').write_text('{}')
  self.lab=SimpleNamespace(ROOT=engine,SOURCE=source,BUILD=self.p/'build',ENV=self.p/'tools',LOCK=dict(ps2recomp=dict(revision='a'*40),resolved_dependencies={}))
 def test_independent_sources_use_only_local_pinned_objects(self):
  def git(command,**kwargs):
   if command[1]=='clone':
    target=Path(command[-1]);target.mkdir();(target/'input.cpp').write_text('pinned source')
   return SimpleNamespace(returncode=0)
  def output(command,**kwargs):return 'commit\n' if 'cat-file' in command else 'a'*40+'\n'
  with patch.object(offline_toolchain,'assert_idle'),patch.object(offline_toolchain.subprocess,'run',side_effect=git) as invoked,patch.object(offline_toolchain.subprocess,'check_output',side_effect=output):
   result=offline_toolchain.stage(self.lab,self.p/'isolated')
  self.assertEqual((self.lab.SOURCE/'input.cpp').read_text(),'private working-tree edit')
  self.assertEqual((self.p/'isolated/dependencies/PS2Recomp/input.cpp').read_text(),'pinned source')
  clone=invoked.call_args_list[0].args[0];self.assertIn('--no-hardlinks',clone);self.assertIn('--no-local',clone)
  self.assertTrue(Path(clone[-2]).is_absolute())
  self.assertFalse(result['network_used']);self.assertFalse(result['historical_binaries_copied'])
 def test_invalid_revision_or_existing_output_stops_before_clone(self):
  self.lab.LOCK['ps2recomp']['revision']='--unsafe'
  with patch.object(offline_toolchain,'assert_idle'),patch.object(offline_toolchain.subprocess,'run') as invoked:
   with self.assertRaisesRegex(ValueError,'Invalid pinned'):offline_toolchain.stage(self.lab,self.p/'isolated')
   invoked.assert_not_called();self.assertFalse((self.p/'isolated').exists())
   with self.assertRaisesRegex(ValueError,'fresh isolated'):offline_toolchain.stage(self.lab,self.lab.SOURCE)
 def test_busy_slot_preserves_everything(self):
  with patch.object(offline_toolchain,'assert_idle',side_effect=RuntimeError('occupied')),patch.object(offline_toolchain.subprocess,'run') as invoked:
   with self.assertRaisesRegex(RuntimeError,'occupied'):offline_toolchain.stage(self.lab,self.p/'isolated')
   invoked.assert_not_called()
 def test_external_disk_metadata_cleanup_keeps_real_and_unknown_indexes(self):
  target=self.p/'staged';pack=target/'.git/objects/pack';pack.mkdir(parents=True)
  real=pack/'pack-abc.idx';real.write_bytes(b'real index')
  sidecar=pack/'._pack-abc.idx';sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  unknown=pack/'._pack-def.idx';unknown.write_bytes(b'unknown index');(pack/'pack-def.idx').write_bytes(b'other index')
  orphan=pack/'._pack-orphan.idx';orphan.write_bytes(bytes.fromhex('0005160700020000'))
  removed=offline_toolchain.clean_pack_metadata(target)
  self.assertEqual(len(removed),1);self.assertFalse(sidecar.exists())
  self.assertEqual(real.read_bytes(),b'real index');self.assertTrue(unknown.exists());self.assertTrue(orphan.exists())
  self.assertEqual(offline_toolchain.clean_pack_metadata(target),[])
 def test_checkout_metadata_never_removes_a_tracked_file(self):
  target=self.p/'checkout';target.mkdir();(target/'source.c').write_text('int source;')
  sidecar=target/'._source.c';sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  with patch.object(offline_toolchain.subprocess,'check_output',return_value='._source.c\0source.c\0'):
   self.assertEqual(offline_toolchain.clean_checkout_metadata(target),[])
  self.assertTrue(sidecar.exists())
  with patch.object(offline_toolchain.subprocess,'check_output',return_value='source.c\0'):
   self.assertEqual(len(offline_toolchain.clean_checkout_metadata(target)),1)
  self.assertFalse(sidecar.exists())
 def test_post_patch_cleanup_requires_matching_isolation_ownership(self):
  self.lab.WORK=self.p
  sidecar=self.lab.SOURCE/'._input.cpp';sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  record=dict(status='locked_public_sources_staged_not_built',lock_sha256=offline_toolchain.sha(self.lab.ROOT/'toolchain.lock.json'),sources=[dict(path=str(self.lab.SOURCE))])
  receipt=self.p/'offline-source-stage.json';receipt.write_text(json.dumps(record))
  with patch.object(offline_toolchain.subprocess,'check_output',return_value='input.cpp\0'):
   result=offline_toolchain.clean_isolated_metadata(self.lab)
  self.assertFalse(sidecar.exists());self.assertEqual(len(result['removed'][str(self.lab.SOURCE)]),1)
  sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  record['sources']=[dict(path=str(self.p.parent))];receipt.write_text(json.dumps(record))
  with self.assertRaisesRegex(ValueError,'ownership'):offline_toolchain.clean_isolated_metadata(self.lab)
  self.assertTrue(sidecar.exists())
