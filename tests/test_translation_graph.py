from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
import knowledge,native_graph,translation_graph as replay
from source_graph import stage
from clean_build import sha,inspect

EE='''void function(uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) {
// 0x1000: 0x05000001 bltz
const bool branch_taken_0x1000 = (GPR_S32(ctx, 8) < 0);
}
'''

class TranslationGraph(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.private=Path(self.tmp.name).resolve()
  src=self.private/'source';src.mkdir();(src/'a.cpp').write_text(EE)
  (src/'override.cxx').write_text(EE.replace('void function','void private_function')+'float preserved_title_clock = 0.0334f;\n')
  (src/'iop.cpp').write_text('// R5900Context* ctx documentation\nconst char* docs="R5900Context* ctx";\n'+EE.replace('R5900Context','IOPContext'))
  config=self.private/'source.json';config.write_text(json.dumps(dict(schema=1,roots=dict(core=str(src)),units=[dict(id=n,source=str(src/file)) for n,file in [('main','a.cpp'),('override','override.cxx'),('iop','iop.cpp')]],targets=[dict(id='runner',kind='executable',units=['main','override','iop'])])))
  self.graph=self.private/'graph';stage(config,self.graph);self.recipe=self.graph/'recipe.json';self.ledger=self.graph/'staging.json'
  data=json.loads(self.recipe.read_text());data['source_contracts']=[dict(id='preserved-title-clock',unit='override',target='runner',required_fragments=['0.0334f'])];self.recipe.write_text(json.dumps(data))
  rows=json.loads(self.ledger.read_text());rows['recipe_sha256']=sha(self.recipe);self.ledger.write_text(json.dumps(rows))
  generated=self.private/'generated';generated.mkdir();(generated/'a.cpp').write_text(EE.replace('GPR_S32','GPR_S64'))
  self.pins={'a.cpp':sha(generated/'a.cpp')}
  disc=self.private/'disc-root';disc.mkdir();self.exe=disc/'TEST.ELF';self.exe.write_bytes(b'synthetic title identity')
  self.identity=sha(self.exe)
  self.profile=dict(expected_executable='TEST.ELF',expected_sha256=self.identity,native_source_graph=dict(recipe='graph/recipe.json',sha256=sha(self.recipe),staging='graph/staging.json',staging_sha256=sha(self.ledger)))
  analysis=self.private/'analysis';analysis.mkdir();(analysis/'translation-stage.json').write_text(json.dumps(dict(accepted=True,generated_sources=self.pins,translation_rule_implementation=knowledge.translation_implementation())))
 def test_replays_qualified_rules_preserves_title_edit_and_never_widens_iop(self):
  effective,entry=replay.prepare_native(self.profile,self.private)
  recipe=self.private/effective['native_source_graph']['recipe'];graph=inspect(recipe)
  self.assertIn('GPR_S64',(graph['root']/'sources/core/a.cpp').read_text())
  override=(graph['root']/'sources/core/override.cxx').read_text();self.assertIn('GPR_S64',override);self.assertIn('0.0334f',override)
  self.assertIn('GPR_S32',(graph['root']/'sources/core/iop.cpp').read_text())
  self.assertEqual((self.private/'source/a.cpp').read_text(),EE)
  self.assertIn('GPR_S32',(self.graph/'sources/core/a.cpp').read_text())
  receipt=replay.verify_receipt(entry,self.private);self.assertEqual(receipt['counts']['corrected'],2)
  ledger=json.loads((self.private/effective['native_source_graph']['staging']).read_text());row=next(r for r in ledger['files'] if r['staged']=='sources/core/a.cpp')
  self.assertEqual(row['original_sha256'],self.pins['a.cpp']);self.assertEqual(row['sha256'],self.pins['a.cpp'])
  self.assertEqual(replay.prepare_native(self.profile,self.private),(effective,entry))
 def test_unknown_source_difference_flags_before_any_source_copy(self):
  fresh=self.private/'generated/a.cpp';fresh.write_text(fresh.read_text()+'int unqualified_difference;\n')
  planned=replay.plan(self.recipe,self.ledger,fresh.parent,self.identity,{'a.cpp':sha(fresh)})
  self.assertEqual(planned['status'],'blocked');self.assertEqual(planned['blockers'][0]['reason'],'no_qualified_staged_copy_of_fresh_translation')
  self.assertIn('GPR_S32',(self.graph/'sources/core/a.cpp').read_text())
 def test_retained_alternate_is_preserved_but_never_mistaken_for_fresh_source(self):
  other='sources/core/alternate.cpp';path=self.graph/other;path.write_text(EE+'int retained_title_hook;\n')
  recipe=json.loads(self.recipe.read_text());recipe['files'][other]=sha(path);self.recipe.write_text(json.dumps(recipe))
  ledger=json.loads(self.ledger.read_text());ledger['recipe_sha256']=sha(self.recipe);ledger['files'].append(dict(original=str(self.private/'old/a.cpp'),original_sha256=sha(path),staged=other,sha256=sha(path)));self.ledger.write_text(json.dumps(ledger))
  planned=replay.plan(self.recipe,self.ledger,self.private/'generated',self.identity,self.pins)
  self.assertEqual(planned['status'],'qualified_not_built');self.assertEqual(len(planned['retained_alternates']),1)
  self.assertNotIn(other,planned['bindings']);self.assertIn('sources/core/a.cpp',planned['bindings'])
 def test_stale_generated_manifest_or_rule_version_cannot_be_replayed(self):
  fresh=self.private/'generated/a.cpp';fresh.write_text(fresh.read_text()+'// tamper\n')
  with self.assertRaisesRegex(ValueError,'manifest changed'):replay.prepare_native(self.profile,self.private)
  fresh.write_text(EE.replace('GPR_S32','GPR_S64'))
  path=self.private/'analysis/translation-stage.json';data=json.loads(path.read_text());data['translation_rule_implementation']={};path.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'predates'):replay.prepare_native(self.profile,self.private)
 def test_output_tampering_cannot_pass_receipt_or_packaging_chain(self):
  effective,entry=replay.prepare_native(self.profile,self.private);spec=effective['native_source_graph']
  receipt=replay.verify_receipt(entry,self.private)
  stage_record=dict(source_graph_recipe_sha256=spec['sha256'],translation_rebase=entry,source_corrections={'path':'old.json'})
  with patch('source_corrections.verify_native_receipt',return_value=dict(graph=dict(sha256=receipt['contract']['recipe_sha256']),contract=dict(recipe_sha256='original'))):native_graph.verify_source_chain(stage_record,self.private)
  with patch('source_corrections.verify_native_receipt',return_value=dict(graph=dict(sha256='0'*64))):
   with self.assertRaisesRegex(ValueError,'does not cover'):native_graph.verify_source_chain(stage_record,self.private)
  graph=self.private/spec['recipe'];(graph.parent/'sources/core/a.cpp').write_text('tamper')
  with self.assertRaises(ValueError):replay.verify_receipt(entry,self.private)
 def test_wrong_title_amendment_is_never_reused(self):
  data=json.loads(self.ledger.read_text());data['source_amendments']=[dict(title_revision=dict(sha256='e'*64))];self.ledger.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'another executable'):replay.plan(self.recipe,self.ledger,self.private/'generated',self.identity,self.pins)

 def test_reviewed_old_checker_receipt_still_checks_source_and_rules(self):
  effective,entry=replay.prepare_native(self.profile,self.private)
  path=self.private/entry['path'];data=json.loads(path.read_text())
  transition=json.loads(Path(replay.__file__).with_name('replay_receipt_versions.json').read_text())['transitions'][0]
  data['contract']['implementation']=transition['from'];path.write_text(json.dumps(data));entry['sha256']=sha(path)
  replay.verify_receipt(entry,self.private)
  self.assertFalse(replay.receipt_implementation_matches(dict(transition['from'],unknown='0'*64),transition['to']))
  self.assertFalse(replay.receipt_implementation_matches(transition['from'],dict(transition['to'],unknown='0'*64)))
  data['contract']['translation_implementation']={};path.write_text(json.dumps(data));entry['sha256']=sha(path)
  with self.assertRaisesRegex(ValueError,'implementation changed'):replay.verify_receipt(entry,self.private)
  data['contract']['translation_implementation']=knowledge.translation_implementation();path.write_text(json.dumps(data));entry['sha256']=sha(path)
  graph=self.private/effective['native_source_graph']['recipe'];(graph.parent/'sources/core/a.cpp').write_text('tampered')
  with self.assertRaises(ValueError):replay.verify_receipt(entry,self.private)

class ResumeTable(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'fn_0x1000.cpp'
  self.body='void fn_0x1000(uint8_t* ram, R5900Context* ctx, PS2Runtime* runtime) {\nlabel_1004:\n// 0x1004: 0x00000000 nop\n}\n'
  self.path.write_text(self.body)
  self.old='// GeneratedFunctionTableInitializer\nconst auto g_ps2RecompiledFunctionTableBase = 0x1000u;\nconst auto g_ps2RecompiledFunctionTableEnd = 0x1100u;\ng_ps2RecompiledFunctionTable[0] = fn_0x1000; // 0x1000\n'
  self.fresh=self.old+'g_ps2RecompiledFunctionTable[1] = fn_0x1000; // 0x1004\n'
 def check(self,old=None,fresh=None,body=None):
  if body is not None:self.path.write_text(body)
  return replay.additive_resume_table(self.old if old is None else old,self.fresh if fresh is None else fresh,{self.path.name:self.path},{self.path.name:sha(self.path)})
 def test_additive_verified_resume(self):self.assertEqual(self.check(),(self.fresh,1))
 def test_changed_existing_target(self):self.assertIsNone(self.check(fresh=self.fresh.replace('[0] = fn_0x1000','[0] = other')))
 def test_unknown_target(self):self.assertIsNone(self.check(fresh=self.fresh.replace('[1] = fn_0x1000','[1] = other')))
 def test_mismatched_slot(self):
  with self.assertRaises(ValueError):self.check(fresh=self.fresh.replace('[1]','[2]'))
 def test_changed_envelope(self):self.assertIsNone(self.check(fresh=self.fresh+'int unrelated;\n'))
 def test_missing_label(self):self.assertIsNone(self.check(body=self.body.replace('label_1004:','label_1008:')))
 def test_label_in_comment_is_not_code(self):self.assertIsNone(self.check(body=self.body.replace('label_1004:','// label_1004:')))
 def test_label_in_other_function_rejected(self):self.assertIsNone(self.check(body=self.body.replace('label_1004:\n','')+'void other() {\nlabel_1004:\n// 0x1004: 0x00000000 nop\n}\n'))
 def test_placeholder_rejected(self):self.assertIsNone(self.check(old=self.old.replace('GeneratedFunctionTableInitializer','placeholder')))
 def test_unsealed_body_rejected(self):
  pins={self.path.name:sha(self.path)};self.path.write_text(self.body+'// changed\n')
  with self.assertRaisesRegex(ValueError,'changed'):replay.additive_resume_table(self.old,self.fresh,{self.path.name:self.path},pins)
