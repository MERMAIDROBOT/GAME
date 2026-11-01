from pathlib import Path
import json,tempfile,unittest
from graph_amend import amend
from source_graph import stage
from clean_build import sha,inspect
class GraphAmend(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  src=self.p/'src';src.mkdir();(src/'main.cpp').write_text('int camera(){return 2;}\n')
  spec=self.p/'stage.json';spec.write_text(json.dumps(dict(schema=1,roots=dict(core=str(src)),units=[dict(id='main',source=str(src/'main.cpp'))],targets=[dict(id='core',kind='static',units=['main'])])))
  self.graph=self.p/'graph';stage(spec,self.graph);self.file='sources/core/main.cpp'
  self.data=dict(schema=1,recipe=str(self.graph/'recipe.json'),recipe_sha256=sha(self.graph/'recipe.json'),staging=str(self.graph/'staging.json'),staging_sha256=sha(self.graph/'staging.json'),edits=[dict(file=self.file,sha256=sha(self.graph/self.file),before='return 2;',after='return 1;',reason='Remove duplicate projection adjustment')])
  self.config=self.p/'amend.json';self.save()
 def save(self):self.config.write_text(json.dumps(self.data))
 def test_title_revision_and_donor_pins_are_enforced_and_retained(self):
  executable=self.p/'game.elf';executable.write_bytes(b'owned executable fixture')
  donor=self.p/'donor.cpp';donor.write_text('int camera(){return 1;}\n')
  self.data.update(title_revision=dict(executable=str(executable),sha256=sha(executable)),source_provenance=dict(path=str(donor),sha256=sha(donor)));self.save()
  amend(self.config,self.p/'out')
  record=json.loads((self.p/'out/staging.json').read_text())['source_amendments'][-1]
  self.assertEqual(record['title_revision'],dict(self.data['title_revision'],executable=str(executable.resolve())));self.assertEqual(record['source_provenance'],dict(self.data['source_provenance'],path=str(donor.resolve())))
  for key,file in [('title_revision',executable),('source_provenance',donor)]:
   original=file.read_bytes();file.write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'input pin changed'):amend(self.config,self.p/'rejected')
   self.assertFalse((self.p/'rejected').exists());file.write_bytes(original)
 def test_malformed_title_identity_is_not_silently_ignored(self):
  for revision in [None,{},dict(executable='relative.elf',sha256='a'*64),dict(executable=str(self.p/'missing'),sha256='not-a-hash')]:
   self.data['title_revision']=revision;self.save()
   with self.assertRaises(ValueError):amend(self.config,self.p/'out')
   self.assertFalse((self.p/'out').exists())
 def test_new_graph_retains_source_origin_and_amendment_without_touching_input(self):
  r=amend(self.config,self.p/'out');g=inspect(self.p/'out/recipe.json');ledger=json.loads((self.p/'out/staging.json').read_text())
  self.assertIn('return 1;',(self.p/'out'/self.file).read_text());self.assertIn('return 2;',(self.graph/self.file).read_text())
  self.assertEqual(ledger['recipe_sha256'],g['recipe_sha256']);self.assertEqual(len(ledger['files'][0]['source_amendments']),1);self.assertFalse(r['equivalence_verified'])
 def test_stale_pin_duplicate_match_and_escaping_edit_are_rejected(self):
  for patch in [dict(sha256='0'*64),dict(before='not present'),dict(file='../outside.cpp'),dict(reason='')]:
   original=dict(self.data['edits'][0]);self.data['edits'][0].update(patch);self.save()
   with self.assertRaises(ValueError):amend(self.config,self.p/'out')
   self.assertFalse((self.p/'out').exists());self.data['edits'][0]=original
 def test_hidden_compiled_payload_cannot_be_amended_into_source_graph(self):
  self.data['edits'][0].update(before='int camera(){return 2;}\n',after='!<arch>\npayload');self.save()
  with self.assertRaises(ValueError):amend(self.config,self.p/'out')

 def test_amendment_cannot_drop_a_carried_implementation(self):
  recipe_path=self.graph/'recipe.json';recipe=json.loads(recipe_path.read_text())
  recipe['source_contracts']=[dict(id='required-service',unit='main',target='core',required_fragments=['return 2;'])]
  recipe_path.write_text(json.dumps(recipe));ledger_path=self.graph/'staging.json';ledger=json.loads(ledger_path.read_text());ledger['recipe_sha256']=sha(recipe_path);ledger_path.write_text(json.dumps(ledger))
  self.data.update(recipe_sha256=sha(recipe_path),staging_sha256=sha(ledger_path));self.save()
  with self.assertRaisesRegex(ValueError,'implementation missing'):amend(self.config,self.p/'out')
  self.assertIn('return 2;',(self.graph/self.file).read_text())
  self.assertFalse((self.p/'out').exists())

 def test_contract_update_is_explicit_and_cannot_remove_existing_obligation(self):
  recipe_path=self.graph/'recipe.json';recipe=json.loads(recipe_path.read_text())
  original=dict(id='required-service',unit='main',target='core',required_fragments=['return 2;'])
  recipe['source_contracts']=[original];recipe_path.write_text(json.dumps(recipe))
  ledger_path=self.graph/'staging.json';ledger=json.loads(ledger_path.read_text());ledger['recipe_sha256']=sha(recipe_path);ledger_path.write_text(json.dumps(ledger))
  self.data.update(recipe_sha256=sha(recipe_path),staging_sha256=sha(ledger_path),source_contracts=[dict(original,required_fragments=['return 1;'])]);self.save()
  amend(self.config,self.p/'updated');self.assertEqual(inspect(self.p/'updated/recipe.json')['source_contracts'],['required-service'])
  self.data['source_contracts']=[];self.save()
  with self.assertRaisesRegex(ValueError,'may not drop'):amend(self.config,self.p/'dropped')
 def test_ambiguous_planned_implementation_fails_before_any_copy(self):
  self.data['edits'][0]['after']='return 1; // return 1;'
  self.data['source_contracts']=[dict(id='new-service',unit='main',target='core',file=self.file,required_fragments=['return 1;'])];self.save()
  for validate_only in (True,False):
   with self.assertRaisesRegex(ValueError,'missing or ambiguous'):amend(self.config,self.p/'never-copied',validate_only=validate_only)
   self.assertFalse((self.p/'never-copied').exists())
