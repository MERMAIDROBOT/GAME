from pathlib import Path
import json,tempfile,unittest,subprocess,sys
from source_graph import stage
from source_corrections import retain,candidates,apply,capture
from graph_amend import amend
from clean_build import sha


class SourceCorrections(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name).resolve()
  self.source=self.p/'source';self.source.mkdir();(self.source/'main.cpp').write_text('int main(){return 2;}\n')
  self.spec=self.p/'spec.json';self.spec.write_text(json.dumps(dict(schema=1,roots=dict(core=str(self.source)),units=[dict(id='main',source=str(self.source/'main.cpp'))],targets=[dict(id='runner',kind='executable',units=['main'])])))
  self.graph=self.p/'graph';stage(self.spec,self.graph)
  self.elf=self.p/'game.elf';self.elf.write_bytes(b'private synthetic executable revision')
  self.config=self.p/'correction.json';self.file='sources/core/main.cpp'
  self.data=dict(schema=1,recipe=str(self.graph/'recipe.json'),recipe_sha256=sha(self.graph/'recipe.json'),staging=str(self.graph/'staging.json'),staging_sha256=sha(self.graph/'staging.json'),title_revision=dict(executable=str(self.elf),sha256=sha(self.elf)),edits=[dict(file=self.file,sha256=sha(self.graph/self.file),before='return 2;',after='return 0;',reason='Synthetic corrected return')])
  self.config.write_text(json.dumps(self.data));self.bank=self.p/'private-bank'
 def test_fresh_staging_automatically_selects_and_replays_revision_correction(self):
  saved=retain(self.config,self.bank);fresh=self.p/'fresh';stage(self.spec,fresh)
  found=candidates(self.bank,self.elf,fresh/'recipe.json');self.assertTrue(found['candidates'][0]['ready'])
  report=apply(self.bank,self.elf,fresh/'recipe.json',fresh/'staging.json',self.p/'corrected')
  self.assertEqual(report['correction_id'],saved['id']);self.assertFalse(report['gameplay_verified'])
  self.assertIn('return 0;',(self.p/'corrected'/self.file).read_text());self.assertIn('return 2;',(fresh/self.file).read_text())
  ledger=json.loads((self.p/'corrected/staging.json').read_text());self.assertEqual(ledger['source_amendments'][-1]['title_revision']['sha256'],sha(self.elf))
 def test_wrong_revision_never_selects_known_correction(self):
  retain(self.config,self.bank);self.elf.write_bytes(b'different game revision')
  self.assertEqual(candidates(self.bank,self.elf,self.graph/'recipe.json')['candidates'],[])
  with self.assertRaisesRegex(ValueError,'found 0'):apply(self.bank,self.elf,self.graph/'recipe.json',self.graph/'staging.json',self.p/'out')
  self.assertFalse((self.p/'out.correction.json').exists())
 def test_metadata_is_not_a_correction_record_but_unknown_files_fail(self):
  entry=retain(self.config,self.bank);path=Path(entry['record']);sidecar=path.with_name('._'+path.name)
  sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  self.assertEqual(len(candidates(self.bank,self.elf,self.graph/'recipe.json')['candidates']),1)
  sidecar.write_bytes(b'unknown data')
  with self.assertRaisesRegex(ValueError,'Invalid correction bank entry'):candidates(self.bank,self.elf,self.graph/'recipe.json')
 def test_source_and_topology_changes_block_selection(self):
  retain(self.config,self.bank);(self.source/'main.cpp').write_text('int main(){return 3;}\n');fresh=self.p/'fresh';stage(self.spec,fresh)
  row=candidates(self.bank,self.elf,fresh/'recipe.json')['candidates'][0];self.assertFalse(row['ready']);self.assertTrue(any(b.startswith('source_pin_changed:') for b in row['blockers']))
  recipe=fresh/'recipe.json';data=json.loads(recipe.read_text());data['units'][0]['flags']=['-O2'];recipe.write_text(json.dumps(data))
  self.assertIn('source_graph_topology_changed',candidates(self.bank,self.elf,recipe)['candidates'][0]['blockers'])
 def test_ambiguous_corrections_require_resolution_and_tampering_is_rejected(self):
  first=retain(self.config,self.bank);self.data['edits'][0]['after']='return 1;';self.config.write_text(json.dumps(self.data));retain(self.config,self.bank)
  with self.assertRaisesRegex(ValueError,'found 2'):apply(self.bank,self.elf,self.graph/'recipe.json',self.graph/'staging.json',self.p/'out')
  Path(first['record']).write_text('{}')
  with self.assertRaisesRegex(ValueError,'pin changed'):candidates(self.bank,self.elf,self.graph/'recipe.json')
 def test_retention_requires_revision_and_does_not_build(self):
  del self.data['title_revision'];self.config.write_text(json.dumps(self.data))
  with self.assertRaisesRegex(ValueError,'require an executable revision'):retain(self.config,self.bank)
  self.assertFalse(self.bank.exists())
 def test_overlapping_destination_does_not_write_into_original_graph(self):
  retain(self.config,self.bank)
  with self.assertRaisesRegex(ValueError,'overlaps'):apply(self.bank,self.elf,self.graph/'recipe.json',self.graph/'staging.json',self.graph/'out')
  self.assertFalse((self.graph/'out.correction.json').exists())
 def test_cpp_entry_point_runs_retention_and_automatic_replay(self):
  entry=Path(__file__).resolve().parents[1]/'run.py'
  def cli(*args):
   result=subprocess.run([sys.executable,str(entry),*map(str,args)],capture_output=True,text=True,timeout=15)
   self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
  self.assertEqual(cli('retain-source-corrections',self.config,'--bank',self.bank)['status'],'private_source_corrections_retained')
  result=cli('apply-source-corrections','--bank',self.bank,'--executable',self.elf,'--recipe',self.graph/'recipe.json','--staging',self.graph/'staging.json','--output',self.p/'out')
  self.assertEqual(result['automatic_selection'],'exact_executable_source_and_topology')
 def test_capture_discovers_source_difference_and_replays_exact_after_bytes(self):
  after=self.p/'after';amend(self.config,after)
  captured=capture(self.elf,self.graph/'recipe.json',self.graph/'staging.json',after/'recipe.json',self.p/'captured.json',bank=self.bank)
  self.assertEqual(captured['changed_files'],[self.file]);self.assertFalse(captured['gameplay_verified'])
  apply(self.bank,self.elf,self.graph/'recipe.json',self.graph/'staging.json',self.p/'replayed')
  self.assertEqual((self.p/'replayed'/self.file).read_bytes(),(after/self.file).read_bytes())
 def test_capture_refuses_unchanged_or_different_graph_topology(self):
  with self.assertRaisesRegex(ValueError,'No changed source'):capture(self.elf,self.graph/'recipe.json',self.graph/'staging.json',self.graph/'recipe.json',self.p/'captured.json')
  after=self.p/'after';amend(self.config,after);recipe=after/'recipe.json';data=json.loads(recipe.read_text());data['units'][0]['flags']=['-O2'];recipe.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'identical source inventory'):capture(self.elf,self.graph/'recipe.json',self.graph/'staging.json',recipe,self.p/'captured.json')
  self.assertFalse((self.p/'captured.json').exists())
 def test_capture_replays_latin1_generated_comments_without_transcoding(self):
  (self.source/'main.cpp').write_bytes(b'// old comment \xff\nint main(){return 2;}\n')
  before=self.p/'latin-before';stage(self.spec,before)
  self.data.update(recipe=str(before/'recipe.json'),recipe_sha256=sha(before/'recipe.json'),staging=str(before/'staging.json'),staging_sha256=sha(before/'staging.json'))
  self.data['edits'][0]['sha256']=sha(before/self.file);self.config.write_text(json.dumps(self.data))
  after=self.p/'latin-after';amend(self.config,after)
  capture(self.elf,before/'recipe.json',before/'staging.json',after/'recipe.json',self.p/'captured.json',bank=self.bank)
  apply(self.bank,self.elf,before/'recipe.json',before/'staging.json',self.p/'replayed')
  self.assertEqual((self.p/'replayed'/self.file).read_bytes(),b'// old comment \xff\nint main(){return 0;}\n')


class InterruptedPreparation(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)/'derived';self.folder.mkdir()
  self.contract=dict(recipe_sha256='a'*64,staging_sha256='b'*64,executable_sha256='c'*64)
  self.manifest=dict(schema=1,recipe_sha256='a'*64,staging_sha256='b'*64,title_revision=dict(sha256='c'*64))
 def test_identified_partial_output_is_preserved_without_reusing_partial_source(self):
  from source_corrections import preserve_interrupted_preparation
  (self.folder/'graph.correction.json').write_text(json.dumps(self.manifest));(self.folder/'partial.cpp').write_bytes(b'partial source')
  backup=Path(preserve_interrupted_preparation(self.folder,self.contract))
  self.assertFalse(self.folder.exists());self.assertEqual((backup/'partial.cpp').read_bytes(),b'partial source')
  self.assertEqual(json.loads((backup/'graph.correction.json').read_text()),self.manifest)
 def test_completed_output_is_never_moved(self):
  from source_corrections import preserve_interrupted_preparation
  (self.folder/'receipt.json').write_text('{}')
  self.assertIsNone(preserve_interrupted_preparation(self.folder,self.contract));self.assertTrue(self.folder.exists())
 def test_unidentified_or_different_revision_output_is_not_moved(self):
  from source_corrections import preserve_interrupted_preparation
  path=self.folder/'graph.correction.json';self.manifest['title_revision']['sha256']='d'*64;path.write_text(json.dumps(self.manifest))
  with self.assertRaisesRegex(ValueError,'Unrecognized'):preserve_interrupted_preparation(self.folder,self.contract)
  self.assertTrue(path.is_file());path.unlink();(self.folder/'unknown').write_text('keep')
  with self.assertRaisesRegex(ValueError,'identity manifest'):preserve_interrupted_preparation(self.folder,self.contract)
 def test_empty_cache_directory_can_be_preserved_and_symlink_cannot(self):
  from source_corrections import preserve_interrupted_preparation
  backup=Path(preserve_interrupted_preparation(self.folder,self.contract));self.assertTrue(backup.is_dir())
  self.folder.symlink_to(backup,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'Unsafe'):preserve_interrupted_preparation(self.folder,self.contract)
