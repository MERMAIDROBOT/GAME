from pathlib import Path
import hashlib,json,tempfile,unittest
from unittest.mock import patch
from generated_corrections import apply,verify


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class GeneratedCorrections(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'generated').mkdir();(self.root/'private-fixes').mkdir();(self.root/'disc-root').mkdir()
  self.elf=self.root/'disc-root/game.elf';self.elf.write_bytes(b'synthetic disc revision')
  self.source=self.root/'generated/test.cpp';self.source.write_text('int test(){return 1;}\n')
  self.fixed=self.root/'private-fixes/test.cpp';self.fixed.write_text('int test(){return 2;}\n')
  self.record=dict(schema=1,kind='revision_generated_corrections',executable_sha256=sha(self.elf),edits=[dict(file='test.cpp',source='private-fixes/test.cpp',before_sha256=sha(self.source),after_sha256=sha(self.fixed),reason='Synthetic changed return')],headers=[])
  self.manifest=self.root/'private-fixes/manifest.json';self.profile=dict(expected_sha256=sha(self.elf))
  self.save();self.mock=patch('pipeline.boot_executable',return_value=self.elf);self.mock.start();self.addCleanup(self.mock.stop)
 def save(self):
  self.manifest.write_text(json.dumps(self.record));self.profile['generated_source_corrections']=dict(path='private-fixes/manifest.json',sha256=sha(self.manifest))
 def test_fresh_translation_replays_and_second_call_is_idempotent(self):
  self.assertEqual(apply(self.profile,self.root)['changed'],['test.cpp']);self.assertEqual(self.source.read_bytes(),self.fixed.read_bytes())
  self.assertEqual(apply(self.profile,self.root)['changed'],[])
  self.source.write_text('int test(){return 1;}\n')
  self.assertEqual(apply(self.profile,self.root)['changed'],['test.cpp'])
 def test_wrong_disc_does_not_patch(self):
  self.elf.write_bytes(b'other revision')
  with self.assertRaisesRegex(ValueError,'exact executable'):apply(self.profile,self.root)
  self.assertIn('return 1;',self.source.read_text())
 def test_changed_source_fails_without_guessing(self):
  self.source.write_text('int different(){return 1;}\n')
  with self.assertRaisesRegex(ValueError,'identity drift'):apply(self.profile,self.root)
 def test_all_files_are_validated_before_first_write(self):
  row=dict(self.record['edits'][0],file='missing.cpp');self.record['edits'].append(row);self.save()
  with self.assertRaisesRegex(ValueError,'identity drift'):apply(self.profile,self.root)
  self.assertIn('return 1;',self.source.read_text())
 def test_manifest_and_replacement_are_pinned(self):
  self.manifest.write_text('{}')
  with self.assertRaisesRegex(ValueError,'manifest pin'):apply(self.profile,self.root)
  self.save();self.fixed.write_text('unreviewed replacement')
  with self.assertRaisesRegex(ValueError,'replacement pin'):apply(self.profile,self.root)
 def test_path_escape_duplicate_and_symlink_rejected(self):
  for name in ['../test.cpp','sub/test.cpp','test.cxx']:
   self.record['edits'][0]['file']=name;self.save()
   with self.assertRaises(ValueError):apply(self.profile,self.root)
  self.record['edits'][0]['file']='test.cpp';self.record['edits'].append(dict(self.record['edits'][0]));self.save()
  with self.assertRaises(ValueError):apply(self.profile,self.root)
  self.record['edits'].pop();self.save();self.source.unlink();self.source.symlink_to(self.fixed)
  with self.assertRaisesRegex(ValueError,'symlink'):apply(self.profile,self.root)
 def test_helper_headers_replayed_and_unknown_existing_header_rejected(self):
  header=self.root/'private-fixes/helper.hpp';header.write_text('constexpr int value=2;\n')
  self.record['headers']=[dict(file='helper.hpp',source='private-fixes/helper.hpp',after_sha256=sha(header),reason='Synthetic helper')];self.save()
  apply(self.profile,self.root);self.assertEqual((self.root/'generated/helper.hpp').read_bytes(),header.read_bytes())
  (self.root/'generated/helper.hpp').write_text('unknown header')
  with self.assertRaisesRegex(ValueError,'identity drift'):apply(self.profile,self.root)

 def test_packaging_rejects_a_stale_runner_or_changed_corrected_sources(self):
  receipt=apply(self.profile,self.root);verify(self.profile,self.root,receipt)
  with self.assertRaisesRegex(ValueError,'does not include'):verify(self.profile,self.root,None)
  original=self.elf.read_bytes();self.elf.write_bytes(b'changed revision after build')
  with self.assertRaisesRegex(ValueError,'revision changed'):verify(self.profile,self.root,receipt)
  self.elf.write_bytes(original)
  self.source.write_text('changed after build')
  with self.assertRaisesRegex(ValueError,'changed after build'):verify(self.profile,self.root,receipt)

 def test_immutable_texture_staging_preserves_original_and_rejects_replacement(self):
  from native import stage_native_asset
  from launch_config import directory_pin
  assets=self.root/'texture-source';assets.mkdir();(assets/'image').write_bytes(b'original texture')
  target=self.root/'application-support/texture-version';pin=directory_pin(assets)['sha256']
  stage_native_asset(assets,target,pin);self.assertEqual((target/'image').read_bytes(),b'original texture')
  stage_native_asset(assets,target,pin)
  (target/'image').write_bytes(b'changed staged texture')
  with self.assertRaisesRegex(ValueError,'immutable native asset'):stage_native_asset(assets,target,pin)
  self.assertEqual((assets/'image').read_bytes(),b'original texture')

 def test_pristine_bytes_are_retained_automatically_before_replay(self):
  before=self.source.read_bytes();pin=sha(self.source)
  receipt=apply(self.profile,self.root)
  baseline=self.root/'analysis/generated-correction-baselines'/(pin+'.cpp')
  self.assertEqual(baseline.read_bytes(),before)
  self.assertEqual(receipt['baselines_retained'][0]['sha256'],pin)
  apply(self.profile,self.root);self.assertEqual(baseline.read_bytes(),before)
 def test_corrupt_retained_baseline_blocks_before_source_change(self):
  pin=sha(self.source);baseline=self.root/'analysis/generated-correction-baselines'/(pin+'.cpp')
  baseline.parent.mkdir(parents=True);baseline.write_bytes(b'incorrect retained bytes')
  with self.assertRaisesRegex(ValueError,'baseline pin changed'):apply(self.profile,self.root)
  self.assertIn('return 1;',self.source.read_text())
