from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import preserved_disc as intake
import pipeline as lab
from test_feedback import elf

class PreservedDisc(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.source=self.root/'source';self.source.mkdir()
  (self.source/'TEST.ELF').write_bytes(elf());(self.source/'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
  (self.source/'DATA.BIN').write_bytes(b'original data')
  self.rows=[dict(path=p.name,bytes=p.stat().st_size,sha256=lab.digest(p),status='copied') for p in sorted(self.source.iterdir())]
  self.manifest=self.root/'preserved.json';self.manifest.write_text(json.dumps({'source':'old mounted disc','files':self.rows,'total_bytes':sum(r['bytes'] for r in self.rows)}))
  self.profile={'id':'synthetic','expected_executable':'TEST.ELF','expected_sha256':lab.digest(self.source/'TEST.ELF')}
  self.private=self.root/'new-private'
  idle=patch.object(intake,'assert_idle');idle.start();self.addCleanup(idle.stop)
 def run_import(self):return intake.import_copy(self.source,self.manifest,lab.digest(self.manifest),self.profile,self.private)
 def test_only_original_selected_files_are_imported_and_pipeline_verify_passes(self):
  saves=self.source/'mc0';saves.mkdir();(saves/'save').write_bytes(b'preserve save')
  (self.source/'diagnostic.txt').write_bytes(b'preserve diagnostic')
  result=self.run_import();self.assertEqual(result['selected_files'],3)
  self.assertEqual({p.name for p in (self.private/'disc-root').iterdir()},{r['path'] for r in self.rows})
  self.assertEqual((saves/'save').read_bytes(),b'preserve save')
  self.assertFalse(result['raw_sector_image']);self.assertFalse(result['saves_migrated'])
  lab.verify(self.profile,self.private)
 def test_modified_original_bytes_fail_and_keep_attempt(self):
  (self.source/'DATA.BIN').write_bytes(b'changed! data')
  with self.assertRaisesRegex(ValueError,'bytes differ'):self.run_import()
  self.assertFalse((self.private/'disc-root').exists())
  result=json.loads((self.private/'analysis/preserved-disc-import.json').read_text())
  self.assertEqual(result['status'],'failed_attempt_preserved');self.assertTrue(Path(result['attempt']).is_dir())
 def test_wrong_manifest_pin_and_incomplete_copy_fail_before_output(self):
  with self.assertRaisesRegex(ValueError,'pin mismatch'):intake.import_copy(self.source,self.manifest,'0'*64,self.profile,self.private)
  d=json.loads(self.manifest.read_text());d['complete']=False;self.manifest.write_text(json.dumps(d))
  with self.assertRaisesRegex(ValueError,'incomplete'):self.run_import()
  self.assertFalse(self.private.exists())
 def test_duplicate_escape_and_source_symlink_are_rejected(self):
  original=self.manifest.read_text()
  for which in ('duplicate','escape','symlink'):
   with self.subTest(which=which):
    d=json.loads(original)
    if which=='duplicate':d['files'].append(dict(d['files'][0]))
    elif which=='escape':d['files'][0]['path']='../outside'
    else:
     p=self.source/'DATA.BIN';p.unlink();p.symlink_to(self.root/'outside')
    self.manifest.write_text(json.dumps(d))
    with self.assertRaises(ValueError):self.run_import()
    self.assertFalse(self.private.exists())
 def test_existing_destination_or_save_is_never_replaced(self):
  (self.private/'disc-root/mc0').mkdir(parents=True);save=self.private/'disc-root/mc0/save';save.write_bytes(b'newer')
  with self.assertRaisesRegex(ValueError,'fresh'):self.run_import()
  self.assertEqual(save.read_bytes(),b'newer')
 def test_busy_guard_prevents_copy(self):
  with patch.object(intake,'assert_idle',side_effect=RuntimeError('occupied')):
   with self.assertRaisesRegex(RuntimeError,'occupied'):self.run_import()
  self.assertFalse(self.private.exists())
 def test_both_products_expose_file_copy_intake(self):
  from target_cli import check_target
  check_target('cpp','import-preserved-copy',system='Linux');check_target('macos','import-preserved-copy',system='Darwin')
