from pathlib import Path
import json,tempfile,unittest
from source_graph import stage
from merge_graphs import merge
from clean_build import sha,inspect

class MergeGraphs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  graphs=[]
  for name in ['one','two']:
   src=self.p/('source-'+name);src.mkdir();(src/'value.h').write_text('#define VALUE 1\n');(src/'main.cpp').write_text('#include "value.h"\nint f(){return VALUE;}\n')
   c=self.p/(name+'.json');c.write_text(json.dumps(dict(schema=1,roots=dict(core=str(src)),units=[dict(id='main',source=str(src/'main.cpp'),include_roots=['core'])],targets=[dict(id='library',kind='static',units=['main'])])))
   out=self.p/('stage-'+name);stage(c,out)
   graphs.append(dict(id=name,recipe=str(out/'recipe.json'),recipe_sha256=sha(out/'recipe.json'),staging=str(out/'staging.json'),staging_sha256=sha(out/'staging.json')))
  self.config=self.p/'merge.json';self.data=dict(schema=1,graphs=graphs);self.save()
 def save(self):self.config.write_text(json.dumps(self.data))
 def test_merge_preserves_title_revision_obligations_and_remaps_changed_files(self):
  record=self.data['graphs'][0];path=Path(record['staging']);ledger=json.loads(path.read_text())
  ledger['source_amendments']=[dict(config_sha256='c'*64,files=['sources/core/main.cpp'],title_revision=dict(executable='/private/disc/game.elf',sha256='a'*64))]
  path.write_text(json.dumps(ledger));record['staging_sha256']=sha(path);self.save()
  report=merge(self.config,self.p/'combined');amendment=report['source_amendments'][0]
  self.assertEqual(amendment['title_revision']['sha256'],'a'*64)
  self.assertEqual(amendment['files'],['one/sources/core/main.cpp'])
 def test_merges_units_targets_and_source_provenance_without_rewriting_relative_includes(self):
  r=merge(self.config,self.p/'combined');g=inspect(self.p/'combined/recipe.json')['recipe']
  self.assertEqual([u['id'] for u in g['units']],['one.main','two.main'])
  self.assertEqual(g['targets'][0]['units'],['one.main']);self.assertEqual(r['source_files'],4)
  self.assertEqual((self.p/'combined/one/sources/core/main.cpp').read_text(),'#include "value.h"\nint f(){return VALUE;}\n')
  self.assertFalse(r['equivalence_verified']);self.assertEqual(r['files'][0]['source_graph'],'one')
 def test_changed_recipe_ledger_and_duplicate_names_block_merge(self):
  self.data['graphs'][1]['id']='one';self.save()
  with self.assertRaises(ValueError):merge(self.config,self.p/'combined')
  self.data['graphs'][1]['id']='two';self.data['graphs'][0]['recipe_sha256']='0'*64;self.save()
  with self.assertRaisesRegex(ValueError,'pin'):merge(self.config,self.p/'combined')
 def test_ledger_must_cover_all_files(self):
  record=self.data['graphs'][0];p=Path(record['staging']);ledger=json.loads(p.read_text());ledger['files']=[];p.write_text(json.dumps(ledger));record['staging_sha256']=sha(p);self.save()
  with self.assertRaisesRegex(ValueError,'every pinned'):merge(self.config,self.p/'combined')
 def test_new_cross_graph_target_is_validated(self):
  self.data['targets']=[dict(id='runner',kind='executable',uses=['one.library','two.library'])];self.save()
  merge(self.config,self.p/'combined');r=inspect(self.p/'combined/recipe.json');self.assertEqual(r['order'][-1],'runner')
  with self.assertRaises(ValueError):merge(self.config,self.p/'combined')
 def test_resume_failed_copy_reuses_only_hash_matching_source(self):
  out=self.p/'combined';out.mkdir();(out/'failed.json').write_text('{"status":"failed"}')
  target=out/'one/sources/core/main.cpp';target.parent.mkdir(parents=True);target.write_bytes((self.p/'stage-one/sources/core/main.cpp').read_bytes())
  r=merge(self.config,out,resume=True);self.assertIn('resumed_failed_receipt_sha256',r)
  self.assertEqual(r['status'],'source_graphs_merged_not_built')
 def test_resume_rejects_changed_existing_source_without_overwriting_it(self):
  out=self.p/'combined';out.mkdir();(out/'failed.json').write_text('{}')
  target=out/'one/sources/core/main.cpp';target.parent.mkdir(parents=True);target.write_text('changed')
  with self.assertRaisesRegex(ValueError,'differs from its pin'):merge(self.config,out,resume=True)
  self.assertEqual(target.read_text(),'changed')

 def test_merge_carries_required_implementations_and_renames_link_membership(self):
  record=self.data['graphs'][0];path=Path(record['recipe']);recipe=json.loads(path.read_text())
  recipe['source_contracts']=[dict(id='retained-service',unit='main',target='library',required_fragments=['int f(){return VALUE;}'])]
  path.write_text(json.dumps(recipe));record['recipe_sha256']=sha(path)
  ledger=Path(record['staging']);data=json.loads(ledger.read_text());data['recipe_sha256']=sha(path);ledger.write_text(json.dumps(data));record['staging_sha256']=sha(ledger);self.save()
  merge(self.config,self.p/'combined');checked=inspect(self.p/'combined/recipe.json')
  self.assertEqual(checked['source_contracts'],['one.retained-service'])
  contract=checked['recipe']['source_contracts'][0]
  self.assertEqual((contract['unit'],contract['target']),('one.main','one.library'))

 def test_merge_reconciles_legacy_headers_introduced_by_another_graph(self):
  canonical=(Path(__file__).resolve().parents[1]/'runtime/native_audio/ps2_audio.h').read_text()
  legacy=canonical.replace('#include <span>\n','').replace('    void setAudioReady(bool ready);\n    void submitPcm(std::span<const int16_t> samples);\n    void resetPcm();','    void setAudioReady(bool ready) { m_audioReady = ready; }')
  graphs=[]
  for name,header,body in [('modern',canonical,'void PS2AudioBackend::setAudioReady(bool ready) {} void PS2AudioBackend::submitPcm(int x) {}'),('old',legacy,'// Generated callers use legacy snapshot')]:
   src=self.p/name;src.mkdir();(src/'ps2_audio.h').write_text(header);(src/'main.cpp').write_text(body)
   config=self.p/(name+'-source.json');config.write_text(json.dumps(dict(schema=1,roots=dict(core=str(src)),units=[dict(id='unit',source=str(src/'main.cpp'),include_roots=['core'])],targets=[dict(id='lib',kind='static',units=['unit'])])))
   out=self.p/(name+'-graph');stage(config,out)
   graphs.append(dict(id=name,recipe=str(out/'recipe.json'),recipe_sha256=sha(out/'recipe.json'),staging=str(out/'staging.json'),staging_sha256=sha(out/'staging.json')))
  self.data=dict(schema=1,graphs=graphs,targets=[dict(id='game',kind='executable',uses=['modern.lib','old.lib'])]);self.save()
  result=merge(self.config,self.p/'combined');root=self.p/'combined'
  self.assertEqual((root/'old/sources/core/ps2_audio.h').read_text(),canonical)
  self.assertEqual((self.p/'old/ps2_audio.h').read_text(),legacy)
  recipe=inspect(root/'recipe.json')['recipe']
  self.assertTrue(any(c.get('header_basename')=='ps2_audio.h' for c in recipe['source_contracts']))
  row=next(r for r in result['files'] if r['staged']=='old/sources/core/ps2_audio.h')
  self.assertEqual(row['original_sha256'],__import__('hashlib').sha256(legacy.encode()).hexdigest())
  self.assertEqual(row['sha256'],sha(root/row['staged']))
