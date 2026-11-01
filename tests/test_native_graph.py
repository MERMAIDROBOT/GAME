from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import native_graph,native
from clean_build import sha
class NativeGraph(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  guard=patch.object(native,'assert_runtime_slot_idle');guard.start();self.addCleanup(guard.stop)
  self.private=self.p/'game';self.private.mkdir();(self.private/'generated').mkdir()
  generated=self.private/'generated/main.cpp';generated.write_text('int main(){return 0;}\n')
  root=self.private/'graph';root.mkdir();(root/'main.cpp').write_bytes(generated.read_bytes());(root/'test.cpp').write_text('int main(){return 0;}\n')
  files={n:sha(root/n) for n in ['main.cpp','test.cpp']}
  recipe={'schema':1,'source_only':True,'files':files,'units':[{'id':'main','source':'main.cpp'},{'id':'test','source':'test.cpp'}],'targets':[{'id':'runner','kind':'executable','units':['main']},{'id':'checks','kind':'executable','units':['test']}]}
  self.recipe=root/'recipe.json';self.recipe.write_text(json.dumps(recipe))
  self.ledger=root/'staging.json';self.ledger.write_text(json.dumps(dict(recipe_sha256=sha(self.recipe),files=[dict(original=str(generated),original_sha256=sha(generated),staged='main.cpp',sha256=sha(root/'main.cpp'))])))
  self.profile={'title':'Example','id':'example','native_source_graph':{'recipe':'graph/recipe.json','sha256':sha(self.recipe),'runner':'runner','tests':['checks'],'staging':'graph/staging.json','staging_sha256':sha(self.ledger)}}
 def test_native_build_rejects_active_play_before_expensive_source_validation(self):
  with patch.object(native,'assert_runtime_slot_idle',side_effect=RuntimeError('active game')),patch.object(native.lab,'require_accepted_translation') as validation:
   with self.assertRaisesRegex(RuntimeError,'active game'):native.build_native(self.profile,self.private)
   validation.assert_not_called()
  self.assertFalse((self.private/'analysis/native-build-stage.json').exists())
 def test_runtime_conformance_refuses_a_busy_engine_slot(self):
  from compute_slot import slot
  with slot('other-build'):
   with self.assertRaisesRegex(RuntimeError,'compute slot'):
    native.test_runtime(self.profile,self.private)
  self.assertFalse((self.private/'analysis/runtime-tests-stage.json').exists())
 def test_runtime_checks_preserve_state_when_game_is_active(self):
  with patch.object(native,'assert_runtime_slot_idle',side_effect=RuntimeError('slot is occupied')):
   with self.assertRaisesRegex(RuntimeError,'occupied'):
    native.test_runtime(self.profile,self.private)
  self.assertFalse((self.private/'analysis/runtime-tests-stage.json').exists())
 def test_pinned_graph_and_translation_bridge(self):
  r=native_graph.specification(self.profile,self.private);self.assertEqual(r['required_generated'],['main.cpp'])
  (self.private/'generated/main.cpp').write_text('changed')
  with self.assertRaisesRegex(ValueError,'generated source'):native_graph.specification(self.profile,self.private)
 def test_title_amendment_cannot_be_reused_under_another_profile_revision(self):
  ledger=json.loads(self.ledger.read_text());ledger['source_amendments']=[dict(files=['main.cpp'],title_revision=dict(executable='/old/location/game.elf',sha256='a'*64))]
  self.ledger.write_text(json.dumps(ledger));self.profile['native_source_graph']['staging_sha256']=sha(self.ledger)
  for identity in [None,'b'*64]:
   self.profile['expected_sha256']=identity
   with self.assertRaisesRegex(ValueError,'different executable revision'):native_graph.specification(self.profile,self.private)
  self.profile['expected_sha256']='a'*64
  self.assertEqual(native_graph.specification(self.profile,self.private)['required_generated'],['main.cpp'])
 def correction_profile(self):
  from source_corrections import retain
  from launch_config import directory_pin
  disc=self.private/'disc-root';disc.mkdir();elf=disc/'TEST.ELF';elf.write_bytes(b'synthetic executable revision')
  self.profile.update(expected_executable='TEST.ELF',expected_sha256=sha(elf))
  ledger=json.loads(self.ledger.read_text());test=self.recipe.parent/'test.cpp';ledger['files'].append(dict(original=str(test),original_sha256=sha(test),staged='test.cpp',sha256=sha(test)));self.ledger.write_text(json.dumps(ledger));self.profile['native_source_graph']['staging_sha256']=sha(self.ledger)
  config=self.private/'correction.json';config.write_text(json.dumps(dict(schema=1,recipe=str(self.recipe),recipe_sha256=sha(self.recipe),staging=str(self.ledger),staging_sha256=sha(self.ledger),title_revision=dict(executable=str(elf),sha256=sha(elf)),edits=[dict(file='main.cpp',sha256=sha(self.recipe.parent/'main.cpp'),before='return 0;',after='return 7;',reason='Synthetic retained correction')])))
  correction=json.loads(config.read_text());correction['source_contracts']=[dict(id='corrected-return',unit='main',target='runner',required_fragments=['return 7;'])];config.write_text(json.dumps(correction))
  self.profile['native_source_graph']['required_source_contracts']=['corrected-return']
  bank=self.private/'corrections';retain(config,bank)
  self.profile['native_source_graph']['corrections']=dict(bank='corrections',sha256=directory_pin(bank)['sha256'])
  return bank,elf
 def fake_corrected_build(self,recipe_path,out,**kwargs):
  checked=native_graph.inspect(recipe_path);self.assertIn('return 7;',(checked['root']/'main.cpp').read_text())
  out.mkdir(exist_ok=True);artifacts={}
  for name in ['runner','checks']:
   path=out/name;path.write_bytes(b'synthetic nonexecuted artifact');artifacts[name]=dict(path=str(path),sha256=sha(path))
  return dict(recipe_sha256=checked['recipe_sha256'],artifacts=artifacts,dependencies=dict(main=['main.cpp'],test=['test.cpp']))
 def test_native_build_automatically_consumes_declared_corrections_and_preserves_profile(self):
  self.correction_profile();before=json.dumps(self.profile,sort_keys=True)
  with patch.object(native_graph,'run',side_effect=self.fake_corrected_build):
   first=native_graph.build(self.profile,self.private);second=native_graph.build(self.profile,self.private)
  self.assertIsNotNone(first['source_corrections']);self.assertEqual(first['source_corrections'],second['source_corrections'])
  self.assertEqual(json.dumps(self.profile,sort_keys=True),before);self.assertIn('return 0;',(self.recipe.parent/'main.cpp').read_text())
 def test_correction_bank_or_actual_disc_changes_block_before_compile(self):
  bank,elf=self.correction_profile();original=elf.read_bytes();elf.write_bytes(b'another revision')
  with patch.object(native_graph,'run') as compiler:
   with self.assertRaisesRegex(ValueError,'actual pinned executable'):native_graph.build(self.profile,self.private)
   compiler.assert_not_called()
  elf.write_bytes(original);next(bank.glob('*.json')).write_text('{}')
  with patch.object(native_graph,'run') as compiler:
   with self.assertRaisesRegex(ValueError,'bank differs'):native_graph.build(self.profile,self.private)
   compiler.assert_not_called()
 def test_changed_prepared_source_blocks_resume_and_packaging(self):
  self.correction_profile()
  with patch.object(native_graph,'run',side_effect=self.fake_corrected_build):stage=native_graph.build(self.profile,self.private)
  (Path(stage['source_graph_recipe']).parent/'main.cpp').write_text('changed')
  with patch.object(native_graph,'run') as compiler:
   with self.assertRaises(ValueError):native_graph.build(self.profile,self.private)
   compiler.assert_not_called()
  with self.assertRaises(ValueError):native.verify_graph_conformance(stage,self.private)
 def test_next_game_resume_pins_corrections_and_refuses_changed_bank(self):
  import next_game
  bank,_=self.correction_profile()
  with patch.object(native_graph,'run',side_effect=self.fake_corrected_build):stage=native_graph.build(self.profile,self.private)
  (self.private/'analysis/native-build-stage.json').write_text(json.dumps(stage))
  observed=next_game.native_artifact_outputs(self.private,'build-native')
  self.assertTrue(any(name.startswith('source-corrections:') for name in observed))
  next(bank.glob('*.json')).write_text('{}')
  with self.assertRaisesRegex(ValueError,'bank changed'):next_game.native_artifact_outputs(self.private,'build-native')
 def test_cached_corrections_do_not_hide_modified_base_source(self):
  self.correction_profile()
  with patch.object(native_graph,'run',side_effect=self.fake_corrected_build):native_graph.build(self.profile,self.private)
  (self.recipe.parent/'main.cpp').write_text('changed base source')
  with patch.object(native_graph,'run') as compiler:
   with self.assertRaises(ValueError):native_graph.build(self.profile,self.private)
   compiler.assert_not_called()
 def test_declared_platform_sdk_is_carried_into_native_packaging_inputs(self):
  import platform_sdks
  self.correction_profile();library=self.private/'libMoltenVK.dylib';library.write_bytes(b'synthetic SDK fixture')
  self.profile['native_source_graph']['platform_sdks']=[dict(kind='moltenvk',path=library.name,sha256=sha(library))]
  with patch.object(platform_sdks,'inspect_library'),patch.object(native_graph,'run',side_effect=self.fake_corrected_build):
   stage=native_graph.build(self.profile,self.private)
   self.assertEqual(native_graph.framework_inputs(stage,self.private)['libMoltenVK.dylib'],library.resolve())
  self.assertFalse(stage['frameworks']['libMoltenVK.dylib']['platform_sdk']['source_build_verified'])
 def abi_profile(self):
  config=self.private/'abi.json'
  probes=[dict(id=n,recipe=str(self.recipe.resolve()),recipe_sha256=sha(self.recipe),unit=n,headers=['packet.h']) for n in ['main','test']]
  config.write_text(json.dumps(dict(schema=1,records=[dict(record='Packet',probes=probes)])))
  self.profile['native_source_graph']['abi_layouts']=dict(config='abi.json',sha256=sha(config))
  return config
 def test_declared_abi_mismatch_blocks_native_build_before_linking(self):
  self.abi_profile()
  with patch('abi_layouts.audit',return_value=dict(status='abi_layouts_mismatch')),patch.object(native_graph,'run') as build:
   with self.assertRaisesRegex(ValueError,'ABI layouts differ'):native_graph.build(self.profile,self.private)
   build.assert_not_called()
 def test_abi_probes_cannot_certify_an_unrelated_source_graph(self):
  config=self.abi_profile();data=json.loads(config.read_text());data['records'][0]['probes'][0]['recipe']='/tmp/another-recipe.json';config.write_text(json.dumps(data));self.profile['native_source_graph']['abi_layouts']['sha256']=sha(config)
  with self.assertRaisesRegex(ValueError,'actual native graph'):native_graph.specification(self.profile,self.private)

 def test_missing_tests_and_changed_recipe_rejected(self):
  self.profile['native_source_graph']['tests']=[]
  with self.assertRaises(ValueError):native_graph.specification(self.profile,self.private)
  self.recipe.write_text('{}')
  with self.assertRaisesRegex(ValueError,'profile pin'):native_graph.specification(self.profile,self.private)
 def test_framework_tampering_and_escape_rejected(self):
  f=self.private/'libgpu.dylib';f.write_bytes(b'local library');record={'frameworks':{'libgpu.dylib':{'path':str(f),'sha256':sha(f)}}}
  self.assertEqual(native_graph.framework_inputs(record,self.private)['libgpu.dylib'],f)
  f.write_bytes(b'changed')
  with self.assertRaises(ValueError):native_graph.framework_inputs(record,self.private)
  f=self.p/'outside.dylib';f.write_bytes(b'outside');record['frameworks']['libgpu.dylib']={'path':str(f),'sha256':sha(f)}
  with self.assertRaisesRegex(ValueError,'private source'):native_graph.framework_inputs(record,self.private)
 def test_packager_refuses_existing_app_before_writes(self):
  (self.private/'analysis').mkdir();binary=self.private/'runner';binary.write_bytes(b'runner')
  (self.private/'analysis/native-build-stage.json').write_text(json.dumps({'binary':str(binary),'sha256':sha(binary)}))
  root=self.p/'tool';root.mkdir();app=self.p/'Example (Debug).app';app.mkdir();sentinel=app/'keep';sentinel.write_text('unchanged')
  with patch.object(native.lab,'ROOT',root),patch.object(native,'logged') as commands:
   with self.assertRaisesRegex(ValueError,'destination already exists'):native.package_local(self.profile,self.private)
   commands.assert_not_called()
  self.assertEqual(sentinel.read_text(),'unchanged')
 def test_profile_contract_obligation_survives_recipe_replacement(self):
  self.profile['native_source_graph']['required_source_contracts']=['retained-service']
  with self.assertRaisesRegex(ValueError,'dropped required source contracts'):
   native_graph.specification(self.profile,self.private)

 def test_graph_build_must_compile_generated_inputs(self):
  built={'artifacts':{},'dependencies':{},'recipe_sha256':sha(self.recipe)}
  with patch.object(native_graph,'run',return_value=built):
   with self.assertRaisesRegex(ValueError,'did not compile'):native_graph.build(self.profile,self.private)

 def test_packager_copies_only_receipt_pinned_frameworks(self):
  (self.private/'disc-manifest.json').write_text(json.dumps({'files':[]}))
  (self.private/'analysis').mkdir();binary=self.private/'runner';binary.write_bytes(b'runner')
  library=self.private/'libgpu.dylib';library.write_bytes(b'compiled library fixture')
  stage={'binary':str(binary),'sha256':sha(binary),'frameworks':{'libgpu.dylib':{'path':str(library),'sha256':sha(library)}},'source_graph_recipe_sha256':sha(self.recipe),'runtime_verified':True}
  checks=self.private/'checks';checks.write_bytes(b'synthetic checks')
  log=self.private/'checks.log';log.write_text('synthetic pass')
  stage['graph_tests']={'checks':dict(path=str(checks),sha256=sha(checks))}
  test_receipt=self.private/'analysis/runtime-tests-stage.json'
  test_receipt.write_text(json.dumps(dict(success=True,source_graph_recipe_sha256=stage['source_graph_recipe_sha256'],runner_sha256=stage['sha256'],tests={'checks':dict(sha256=sha(checks),log=str(log),log_sha256=sha(log))})))
  stage['conformance_receipt']=dict(path=str(test_receipt),sha256=sha(test_receipt))
  (self.private/'analysis/native-build-stage.json').write_text(json.dumps(stage))
  root=self.p/'tool';root.mkdir();(root/'CONTROLS.md').write_text('Controls')
  def command(args,*a,**kw):
   if '-o' in args:Path(args[args.index('-o')+1]).write_bytes(b'launcher fixture')
   if str(args[0]).endswith('/codesign'):
    packaged=Path(args[-1])/'Contents'
    for relative in ['MacOS/ps2EntryRunner','Frameworks/libgpu.dylib']:
     item=packaged/relative;item.write_bytes(item.read_bytes()+b' signed fixture')
   return 'success'
  with patch.object(native.lab,'ROOT',root),patch.object(native.lab,'boot_executable',return_value=self.private/'disc-root/game.elf'),patch.object(native,'cd_image',return_value=None),patch.object(native,'logged',side_effect=command):
   native.package_local(self.profile,self.private)
  app=self.p/'Example (Debug).app'
  self.assertEqual((app/'Contents/Frameworks/libgpu.dylib').read_bytes(),library.read_bytes()+b' signed fixture')
  receipt=json.loads((self.private/'analysis/local-package-stage.json').read_text())
  self.assertEqual(receipt['frameworks']['libgpu.dylib'],sha(app/'Contents/Frameworks/libgpu.dylib'))
  self.assertEqual(receipt['source_frameworks']['libgpu.dylib'],sha(library))
  self.assertEqual(receipt['source_build_sha256'],sha(binary))
  self.assertEqual(receipt['build_sha256'],sha(app/'Contents/MacOS/ps2EntryRunner'))
  self.assertNotEqual(receipt['build_sha256'],receipt['source_build_sha256'])
  self.assertFalse(receipt['gameplay_verified'])

 def test_merged_graph_can_compile_either_pinned_copy_of_same_generated_source(self):
  root=self.recipe.parent;original=self.private/'generated/main.cpp'
  (root/'copy.cpp').write_bytes(original.read_bytes())
  recipe=json.loads(self.recipe.read_text());recipe['files']['copy.cpp']=sha(root/'copy.cpp');self.recipe.write_text(json.dumps(recipe))
  ledger=json.loads(self.ledger.read_text());ledger['recipe_sha256']=sha(self.recipe);ledger['files'].append(dict(original=str(original),original_sha256=sha(original),staged='copy.cpp',sha256=sha(root/'copy.cpp')));self.ledger.write_text(json.dumps(ledger))
  spec=self.profile['native_source_graph'];spec.update(sha256=sha(self.recipe),staging_sha256=sha(self.ledger))
  checked=native_graph.specification(self.profile,self.private)
  self.assertEqual(checked['generated_alternatives'],[['main.cpp','copy.cpp']])
  built={'artifacts':{'runner':{'path':'runner','sha256':'fixture'},'checks':{'path':'checks','sha256':'fixture'}},'dependencies':{'main':['copy.cpp']},'recipe_sha256':sha(self.recipe)}
  with patch.object(native_graph,'run',return_value=built) as compiled:
   r=native_graph.build(self.profile,self.private);self.assertFalse(r['gameplay_verified'])
  self.assertEqual(compiled.call_args.kwargs['only_targets'],['runner','checks'])

 def test_unused_or_test_only_generated_source_does_not_certify_runner(self):
  built={'artifacts':{},'dependencies':{'test':['main.cpp']},'recipe_sha256':sha(self.recipe)}
  with patch.object(native_graph,'run',return_value=built):
   with self.assertRaisesRegex(ValueError,'did not compile'):native_graph.build(self.profile,self.private)

 def test_generated_source_in_linked_archive_is_counted(self):
  recipe=json.loads(self.recipe.read_text());recipe['targets'][0]=dict(id='runner',kind='executable',uses=['game'])
  recipe['targets'].append(dict(id='game',kind='static',units=['main']))
  self.recipe.write_text(json.dumps(recipe));ledger=json.loads(self.ledger.read_text());ledger['recipe_sha256']=sha(self.recipe);self.ledger.write_text(json.dumps(ledger))
  self.profile['native_source_graph'].update(sha256=sha(self.recipe),staging_sha256=sha(self.ledger))
  checked=native_graph.specification(self.profile,self.private)
  self.assertEqual(checked['runner_units'],['main']);self.assertEqual(checked['runner_targets'],['game','runner'])
  built={'artifacts':{'runner':{'path':'runner','sha256':'fixture'},'checks':{'path':'checks','sha256':'fixture'}},'dependencies':{'main':['main.cpp'],'test':['test.cpp']},'recipe_sha256':sha(self.recipe)}
  with patch.object(native_graph,'run',return_value=built):
   r=native_graph.build(self.profile,self.private)
  self.assertEqual(r['runner_source_units'],['main']);self.assertFalse(r['gameplay_verified'])

 def test_fresh_generated_directory_resolves_by_pinned_content_not_old_absolute_path(self):
  ledger=json.loads(self.ledger.read_text());ledger['files'][0]['original']='/historical/conversion/generated/main.cpp';self.ledger.write_text(json.dumps(ledger));self.profile['native_source_graph']['staging_sha256']=sha(self.ledger)
  checked=native_graph.specification(self.profile,self.private)
  self.assertEqual(checked['required_generated'],['main.cpp'])
  self.assertEqual(checked['generated_origin_resolution'][0]['resolution'],'pinned_content_identity')
  (self.private/'generated/main.cpp').write_text('int main(){return 9;}\n')
  with self.assertRaisesRegex(ValueError,'generated source'):native_graph.specification(self.profile,self.private)

 def test_revision_gated_generated_substitution_is_explicit_and_byte_pinned(self):
  original=self.private/'generated/main.cpp';entry=dict(source='main.cpp',generated_sha256=sha(original),staged='main.cpp',staged_sha256=sha(self.recipe.parent/'main.cpp'),executable_sha256='a'*64,reason='Qualified title-specific native function adapter')
  self.profile['expected_sha256']='a'*64;self.profile['native_source_graph']['generated_substitutions']=[entry]
  checked=native_graph.specification(self.profile,self.private)
  self.assertEqual(checked['generated_origin_resolution'][0]['resolution'],'revision_gated_substitution')
  for field,value in [('executable_sha256','b'*64),('generated_sha256','b'*64),('staged_sha256','b'*64),('reason','')]:
   prior=entry[field];entry[field]=value
   with self.assertRaises(ValueError):native_graph.specification(self.profile,self.private)
   entry[field]=prior

 def test_substitution_checks_the_actual_disc_before_compilation(self):
  source=self.private/'generated/main.cpp';self.profile['expected_sha256']='a'*64
  self.profile['native_source_graph']['generated_substitutions']=[dict(source='main.cpp',generated_sha256=sha(source),staged='main.cpp',staged_sha256=sha(self.recipe.parent/'main.cpp'),executable_sha256='a'*64,reason='Synthetic gated override')]
  exe=self.private/'other.elf';exe.write_bytes(b'different executable')
  with patch('pipeline.boot_executable',return_value=exe),patch.object(native_graph,'run') as compile:
   with self.assertRaisesRegex(ValueError,'actual disc'):native_graph.build(self.profile,self.private)
   compile.assert_not_called()

 def test_separate_source_component_binding_is_pinned_and_reverified_for_packaging(self):
  receipt=self.private/'component.json';receipt.write_text('{}')
  binary=self.private/'native.dylib';binary.write_bytes(b'fixture')
  artifact=dict(path=str(binary),sha256=sha(binary),component_receipt=dict(path=str(receipt),sha256=sha(receipt)))
  self.profile['native_source_graph']['native_components']=[dict(kind='native_graphics',receipt='component.json',sha256=sha(receipt),framework='libnative.dylib')]
  with patch('native_components.verify',return_value=artifact) as verify:
   graph=native_graph.specification(self.profile,self.private)
   self.assertEqual(graph['component_frameworks'],{'libnative.dylib':artifact})
   self.assertEqual(native_graph.framework_inputs({'frameworks':graph['component_frameworks']},self.private),{'libnative.dylib':binary})
   self.assertEqual(verify.call_count,2)
   receipt.write_text('changed')
   with self.assertRaisesRegex(ValueError,'profile pin'):native_graph.specification(self.profile,self.private)
   with self.assertRaisesRegex(ValueError,'receipt changed'):native_graph.framework_inputs({'frameworks':graph['component_frameworks']},self.private)
 def test_declared_component_is_built_and_verified_before_source_amendment(self):
  self.private=self.private.resolve()
  receipt=self.private/'component.json';receipt.write_text('{}')
  artifact=dict(path=str(self.private/'plugin.dylib'),sha256='a'*64,component_receipt=dict(path=str(receipt),sha256=sha(receipt)))
  configuration={'sources':{}}
  self.profile['native_source_graph'].update(native_components=[dict(kind='native_graphics',build=configuration,framework='libnative.dylib')],corrections={'bank':'not-staged'})
  with patch('native_components.provision',return_value=artifact) as provision,patch('native_components.verify',return_value=artifact) as verify,patch('source_corrections.prepare_native',side_effect=RuntimeError('source staging sentinel')):
   with self.assertRaisesRegex(RuntimeError,'source staging sentinel'):native_graph.build(self.profile,self.private)
   provision.assert_called_once();verify.assert_called_once()
  self.assertIn('build',self.profile['native_source_graph']['native_components'][0])
 def test_duplicate_component_builds_are_rejected_before_compute(self):
  entry=dict(kind='native_graphics',build={'sources':{}},framework='libnative.dylib')
  self.profile['native_source_graph']['native_components']=[entry,dict(entry)]
  with patch('native_components.provision') as provision:
   with self.assertRaisesRegex(ValueError,'duplicate'):native_graph.build(self.profile,self.private)
   provision.assert_not_called()
 def test_component_names_cannot_override_a_graph_provider(self):
  receipt=self.private/'component.json';receipt.write_text('{}')
  self.profile['native_source_graph'].update(native_components=[dict(kind='native_graphics',receipt='component.json',sha256=sha(receipt),framework='libnative.dylib')],frameworks={'libnative.dylib':'runner'})
  with patch('native_components.verify',return_value={'path':str(self.private/'plugin.dylib'),'sha256':'0'*64}):
   with self.assertRaisesRegex(ValueError,'competing source providers'):native_graph.specification(self.profile,self.private)

 def test_surface_enable_cannot_bypass_runner_transport_closure(self):
  self.profile['native_launch']={'environment':{'PS2_PORT_HW_GS_ZERO_COPY':'1'}}
  with self.assertRaisesRegex(ValueError,'staged into the actual source runner'):native_graph.specification(self.profile,self.private)
  ledger=json.loads(self.ledger.read_text());ledger['surface_transport']={'window':'main.cpp','frontend':'test.cpp','headers':{'fake.hpp':'0'*64}}
  self.ledger.write_text(json.dumps(ledger));self.profile['native_source_graph']['staging_sha256']=sha(self.ledger)
  with self.assertRaisesRegex(ValueError,'linked runner sources'):native_graph.specification(self.profile,self.private)


class SourceGraphConformance(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.private=Path(self.tmp.name)
  guard=patch.object(native,'assert_runtime_slot_idle');guard.start();self.addCleanup(guard.stop)
  (self.private/'analysis').mkdir();self.binary=self.private/'checks';self.binary.write_bytes(b'synthetic executable')
  self.record=self.private/'analysis/native-build-stage.json'
  self.stage=dict(source_graph_recipe_sha256='a'*64,sha256='b'*64,runtime_verified=True,
                  graph_tests={'checks':dict(path=str(self.binary),sha256=sha(self.binary))})
  self.record.write_text(json.dumps(self.stage))
 def test_failed_rerun_revokes_old_verification(self):
  with patch.object(native,'logged',side_effect=RuntimeError('synthetic failure')):
   with self.assertRaisesRegex(RuntimeError,'synthetic failure'):native.test_runtime({},self.private)
  self.assertFalse(json.loads(self.record.read_text())['runtime_verified'])
  self.assertFalse(json.loads((self.private/'analysis/runtime-tests-stage.json').read_text())['success'])
 def test_launch_overrides_are_scrubbed_and_test_receipt_pinned(self):
  calls=[]
  def run(command,log,**kwargs):
   calls.append(kwargs['base_environment']);log.parent.mkdir(parents=True,exist_ok=True);log.write_text('synthetic pass');return 'success'
  with patch.object(native,'runtime_environment',return_value={'PATH':'/usr/bin','PS2_PORT_SKIP':'1','PS2X_TEST':'1','DYLD_INSERT_LIBRARIES':'bad'}),patch.object(native,'logged',side_effect=run):
   native.test_runtime({},self.private)
  self.assertEqual(calls,[{'PATH':'/usr/bin'}])
  stage=json.loads(self.record.read_text());self.assertTrue(stage['runtime_verified'])
  receipt=Path(stage['conformance_receipt']['path']);self.assertEqual(sha(receipt),stage['conformance_receipt']['sha256'])
  data=json.loads(receipt.read_text());self.assertEqual(data['tests']['checks']['sha256'],sha(self.binary));self.assertFalse(data['gameplay_verified'])
 def test_changed_test_binary_does_not_certify_build(self):
  def run(command,log,**kwargs):self.binary.write_bytes(b'changed');return 'success'
  with patch.object(native,'logged',side_effect=run):
   with self.assertRaisesRegex(ValueError,'changed during'):native.test_runtime({},self.private)
  self.assertFalse(json.loads(self.record.read_text())['runtime_verified'])
 def test_unsafe_test_log_name_rejected_before_execution(self):
  self.stage['graph_tests']={'../escape':next(iter(self.stage['graph_tests'].values()))};self.record.write_text(json.dumps(self.stage))
  with patch.object(native,'logged') as run:
   with self.assertRaisesRegex(ValueError,'target name'):native.test_runtime({},self.private)
  run.assert_not_called()

 def test_packaging_rejects_missing_receipt_despite_old_verified_flag(self):
  with self.assertRaisesRegex(ValueError,'receipt is missing'):native.verify_graph_conformance(self.stage,self.private)
 def test_packaging_rejects_changed_conformance_log(self):
  def run(command,log,**kwargs):log.parent.mkdir(parents=True,exist_ok=True);log.write_text('pass');return 'success'
  with patch.object(native,'logged',side_effect=run):native.test_runtime({},self.private)
  stage=json.loads(self.record.read_text());native.verify_graph_conformance(stage,self.private)
  (self.private/'logs/graph-test-checks.log').write_text('changed')
  with self.assertRaisesRegex(ValueError,'log is missing or changed'):native.verify_graph_conformance(stage,self.private)

 def test_declared_runtime_modes_are_executed_scrubbed_and_pinned(self):
  cases=[dict(name='sync',environment={'PS2_PORT_HEADLESS':'1','PS2_PORT_GS_THREAD':'0'}),dict(name='worker',environment={'PS2_PORT_HEADLESS':'1','PS2_PORT_GS_THREAD':'1'})]
  self.stage['graph_test_cases']=cases;self.record.write_text(json.dumps(self.stage));calls=[]
  def run(command,log,**kwargs):
   calls.append(kwargs['base_environment']);log.parent.mkdir(parents=True,exist_ok=True);log.write_text('synthetic case pass');return 'success'
  with patch.object(native,'runtime_environment',return_value={'PATH':'/usr/bin','PS2_PORT_SKIP':'1'}),patch.object(native,'logged',side_effect=run):
   native.test_runtime.__wrapped__({},self.private) # fixture only: logged cannot execute a program
  self.assertEqual(calls,[dict(PATH='/usr/bin',**c['environment']) for c in cases])
  stage=json.loads(self.record.read_text());native.verify_graph_conformance(stage,self.private)
  receipt=json.loads(Path(stage['conformance_receipt']['path']).read_text())
  self.assertEqual(receipt['test_cases'],cases)
  case_log=Path(receipt['tests']['checks']['cases'][1]['log']);case_log.write_text('changed')
  with self.assertRaisesRegex(ValueError,'case log is missing or changed'):native.verify_graph_conformance(stage,self.private)
 def test_missing_declared_case_blocks_packaging(self):
  self.stage['graph_test_cases']=[dict(name='worker',environment={'PS2_PORT_GS_THREAD':'1'})];self.record.write_text(json.dumps(self.stage))
  def run(command,log,**kwargs):log.parent.mkdir(parents=True,exist_ok=True);log.write_text('pass');return 'success'
  with patch.object(native,'logged',side_effect=run):native.test_runtime.__wrapped__({},self.private)
  stage=json.loads(self.record.read_text());receipt=Path(stage['conformance_receipt']['path']);data=json.loads(receipt.read_text());data['tests']['checks']['cases']=[];receipt.write_text(json.dumps(data));stage['conformance_receipt']['sha256']=sha(receipt)
  with self.assertRaisesRegex(ValueError,'omits declared runtime cases'):native.verify_graph_conformance(stage,self.private)

class ConformanceCases(unittest.TestCase):
 def test_cases_cannot_add_arbitrary_flags_duplicate_names_or_commands(self):
  for cases in [[dict(name='worker',environment={'PS2_PORT_SKIP_AUDIO':'1'})],[dict(name='../escape',environment={})],[dict(name='worker',environment={'PS2_PORT_GS_THREAD':'2'})],[dict(name='worker',environment={})]*2]:
   with self.assertRaises(ValueError):native_graph.conformance_cases(cases)

class RetainedSourcePolicy(unittest.TestCase):
 setUp=NativeGraph.setUp
 def test_byte_identical_renamed_source_is_counted_with_real_dependency_coverage(self):
  ledger=json.loads(self.ledger.read_text());ledger['files'][0]['original']=str(self.private/'historic/renamed.cpp');ledger['files'][0]['original_sha256']='a'*64;self.ledger.write_text(json.dumps(ledger));self.profile['native_source_graph']['staging_sha256']=sha(self.ledger)
  result=native_graph.specification(self.profile,self.private)
  self.assertEqual(result['generated_origin_resolution'][0]['resolution'],'byte_identical_relocated_source')
  (self.recipe.parent/'main.cpp').write_text('int main(){return 2;}\n')
  with self.assertRaises(ValueError):native_graph.specification(self.profile,self.private)
 def test_lineage_is_ordered_and_must_join_the_actual_graph(self):
  source=dict(kind='source_corrections',path='source.json',sha256='a'*64);replay=dict(kind='translation_rebase',path='replay.json',sha256='b'*64)
  stage=dict(source_graph_recipe_sha256='last',source_lineage=[source,replay])
  with patch('source_corrections.verify_native_receipt',return_value=dict(graph=dict(sha256='middle'),contract=dict(recipe_sha256='first'))),patch('translation_graph.verify_receipt',return_value=dict(graph=dict(sha256='last'),contract=dict(recipe_sha256='middle'))):
   self.assertEqual(native_graph.verify_source_chain(stage,self.private)['source_lineage'],[source,replay])
   with self.assertRaisesRegex(ValueError,'does not cover'):native_graph.verify_source_chain(dict(stage,source_lineage=[replay,source]),self.private)
 def test_bad_source_cache_pin_prevents_compilation(self):
  self.profile['native_source_graph']['source_cache']=dict(build='absent',receipt_sha256='0'*64,recipe='graph/recipe.json',recipe_sha256=sha(self.recipe))
  with patch.object(native_graph,'run') as build:
   with self.assertRaises((ValueError,FileNotFoundError)):native_graph.build(self.profile,self.private)
   build.assert_not_called()

 def test_compiled_byte_identical_alias_qualifies_even_when_original_copy_is_unused(self):
  alias=self.recipe.parent/'alias.cpp';alias.write_bytes((self.private/'generated/main.cpp').read_bytes())
  r=json.loads(self.recipe.read_text());r['files']['alias.cpp']=sha(alias);self.recipe.write_text(json.dumps(r));l=json.loads(self.ledger.read_text());l['recipe_sha256']=sha(self.recipe);l['files'].append(dict(original='/historic/alias.cpp',original_sha256='b'*64,staged='alias.cpp',sha256=sha(alias)));self.ledger.write_text(json.dumps(l));self.profile['native_source_graph'].update(sha256=sha(self.recipe),staging_sha256=sha(self.ledger))
  result=native_graph.specification(self.profile,self.private);self.assertEqual(set(result['generated_alternatives'][0]),{'main.cpp','alias.cpp'})

 def test_generated_source_symlink_is_rejected_even_when_bytes_match(self):
  p=self.private/'generated/main.cpp';p.unlink();p.symlink_to(self.recipe.parent/'main.cpp')
  with self.assertRaisesRegex(ValueError,'symlink'):native_graph.specification(self.profile,self.private)

 def test_derived_origin_alternative_is_retained_when_unused_exact_path_exists(self):
  alias=self.recipe.parent/'amended.cpp';alias.write_text('int main(){return 7;}\n');r=json.loads(self.recipe.read_text());r['files']['amended.cpp']=sha(alias);self.recipe.write_text(json.dumps(r));l=json.loads(self.ledger.read_text());l['recipe_sha256']=sha(self.recipe);l['files'].append(dict(original='/historic/main.cpp',original_sha256=sha(self.private/'generated/main.cpp'),staged='amended.cpp',sha256=sha(alias),source_amendments=[dict(reason='Synthetic revision-pinned amendment')]));self.ledger.write_text(json.dumps(l));self.profile['native_source_graph'].update(sha256=sha(self.recipe),staging_sha256=sha(self.ledger))
  result=native_graph.specification(self.profile,self.private);self.assertEqual(set(result['generated_alternatives'][0]),{'main.cpp','amended.cpp'})
  coverage=native_graph.generated_coverage(result,dict(dependencies=dict(main=['amended.cpp'],test=['main.cpp'])))
  self.assertEqual(coverage['status'],'compiled_source_coverage_complete')
  missing=native_graph.generated_coverage(result,dict(dependencies=dict(main=[],test=['main.cpp','amended.cpp'])))
  self.assertEqual(missing['matched'],0);self.assertEqual(len(missing['missing']),1)
