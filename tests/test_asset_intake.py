from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
import asset_intake as intake
from launch_config import directory_pin,prepare

class AssetIntake(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.source=self.root/'original';self.source.mkdir();(self.source/'a.bin').write_bytes(b'local retail texture');(self.source/'nested').mkdir();(self.source/'nested/b.bin').write_bytes(b'other texture')
  self.private=self.root/'private';self.private.mkdir();self.pin=directory_pin(self.source)['sha256']
 def test_stages_verified_set_and_reuses_without_replacing(self):
  receipt=intake.stage(self.source,self.private,'textures',self.pin)
  self.assertEqual(receipt['files'],2);self.assertFalse(receipt['asset_generation'])
  self.assertEqual(directory_pin(self.private/'textures')['sha256'],self.pin)
  self.assertEqual(intake.stage(self.source,self.private,'textures',self.pin),receipt)
  self.assertEqual((self.source/'a.bin').read_bytes(),b'local retail texture')
 def test_wrong_pin_does_not_copy(self):
  with self.assertRaisesRegex(ValueError,'expected pin'):intake.stage(self.source,self.private,'textures','0'*64)
  self.assertFalse((self.private/'textures').exists())
 def test_unknown_existing_destination_preserved(self):
  target=self.private/'textures';target.mkdir();(target/'a.bin').write_bytes(b'preserve')
  with self.assertRaisesRegex(ValueError,'differs'):intake.stage(self.source,self.private,'textures',self.pin)
  self.assertEqual((target/'a.bin').read_bytes(),b'preserve')
 def test_symlinks_and_escape_rejected(self):
  (self.source/'link').symlink_to(self.source/'a.bin')
  with self.assertRaisesRegex(ValueError,'symlink'):intake.stage(self.source,self.private,'textures',self.pin)
  (self.source/'link').unlink()
  with self.assertRaises(ValueError):intake.stage(self.source,self.private,'../outside',self.pin)
 def test_source_destination_overlap_rejected(self):
  with self.assertRaisesRegex(ValueError,'overlap'):intake.stage(self.source,self.root,'original/nested/new',self.pin)
 def test_valid_appledouble_ignored_unknown_files_kept(self):
  (self.source/'._a.bin').write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00metadata')
  (self.source/'._unknown').write_bytes(b'actual asset')
  pin=directory_pin(self.source)['sha256'];intake.stage(self.source,self.private,'textures',pin)
  self.assertFalse((self.private/'textures/._a.bin').exists());self.assertTrue((self.private/'textures/._unknown').exists())
 def test_mid_copy_failure_keeps_partial_attempt_and_original(self):
  with patch('asset_intake.shutil.copyfileobj',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):intake.stage(self.source,self.private,'textures',self.pin)
  self.assertFalse((self.private/'textures').exists());self.assertTrue(list(self.private.glob('*failure.json')))
  self.assertTrue((self.source/'a.bin').exists())
 def test_launcher_prepares_declared_local_source(self):
  profile=dict(native_launch=dict(paths=dict(PS2_PORT_TEX_PACK=dict(path='textures',source=str(self.source),sha256=self.pin))))
  result=prepare(profile,self.private,{})
  self.assertEqual(result['assets']['PS2_PORT_TEX_PACK']['sha256'],self.pin)
 def test_mutable_cache_cannot_import_assets(self):
  profile=dict(native_launch=dict(paths=dict(PS2_PORT_HW_GS_PIPELINE_CACHE_DIR=dict(path='cache',source=str(self.source)))))
  with self.assertRaises(ValueError):prepare(profile,self.private,{})

 def test_relative_source_rejected(self):
  with self.assertRaisesRegex(ValueError,'absolute'):intake.stage('relative',self.private,'textures',self.pin)
 def test_source_change_during_copy_prevents_publication(self):
  import shutil
  original=shutil.copyfileobj
  def changed(incoming,outgoing,length):
   original(incoming,outgoing,length)
   (self.source/'a.bin').write_bytes(b'changed source texture')
  with patch('asset_intake.shutil.copyfileobj',side_effect=changed):
   with self.assertRaises(ValueError):intake.stage(self.source,self.private,'textures',self.pin)
  self.assertFalse((self.private/'textures').exists())
