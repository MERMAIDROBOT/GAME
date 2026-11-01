from pathlib import Path
import hashlib,json,shutil,tempfile,unittest
from unittest.mock import patch
import clean_build

class CleanBuild(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'src';self.root.mkdir()
  (self.root/'main.cpp').write_text('int main(){return 0;}\n')
  self.data={'schema':1,'source_only':True,'files':{'main.cpp':clean_build.sha(self.root/'main.cpp')},'units':[{'id':'main','source':'main.cpp'}],'targets':[{'id':'program','kind':'executable','units':['main']}]}
  self.recipe=self.root/'recipe.json';self.save()
 def save(self):self.recipe.write_text(json.dumps(self.data))
 def test_plan_has_no_side_effects(self):
  out=Path(self.tmp.name)/'out';r=clean_build.run(self.recipe,out)
  self.assertFalse(out.exists());self.assertFalse(r['historical_objects_used']);self.assertFalse(r['gameplay_verified'])
 def test_exfat_metadata_is_not_source_and_ordinary_unpinned_headers_still_fail(self):
  includes=self.root/'headers';includes.mkdir();h=includes/'value.h';h.write_text('#define VALUE 1\n')
  self.data['files']['headers/value.h']=clean_build.sha(h);self.data['units'][0]['includes']=['headers'];self.save()
  metadata=includes/'._value.h';metadata.write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00'+b'fixture')
  clean_build.inspect(self.recipe)
  with self.assertRaisesRegex(RuntimeError,'unstaged'):clean_build.classify_dependencies([metadata],self.root,self.data['files'],[])
  metadata.write_text('ordinary unpinned C header')
  with self.assertRaisesRegex(ValueError,'Unpinned'):clean_build.inspect(self.recipe)

 def test_changed_source_rejected(self):
  (self.root/'main.cpp').write_text('int main(){return 1;}')
  with self.assertRaises(ValueError):clean_build.inspect(self.recipe)
 def test_prebuilt_rejected(self):
  (self.root/'old.a').write_bytes(b'archive');self.data['files']['old.a']=clean_build.sha(self.root/'old.a');self.save()
  with self.assertRaisesRegex(ValueError,'compiled'):clean_build.inspect(self.recipe)
 def test_compiled_payload_cannot_hide_behind_a_version_or_header_suffix(self):
  for name in ['old.o.v46','opaque.h']:
   p=self.root/name;p.write_bytes(b'\xde\xc0\x17\x0bbitcode');self.data['files'][name]=clean_build.sha(p);self.save()
   with self.assertRaisesRegex(ValueError,'compiled'):clean_build.inspect(self.recipe)
   del self.data['files'][name]

 def test_escape_and_symlink_rejected(self):
  self.data['units'][0]['source']='../escape.cpp';self.save()
  with self.assertRaises(ValueError):clean_build.inspect(self.recipe)
  self.data['units'][0]['source']='main.cpp';self.save()
  (self.root/'main.cpp').unlink();(self.root/'main.cpp').symlink_to('/etc/hosts')
  with self.assertRaises(ValueError):clean_build.inspect(self.recipe)
 def test_target_cycle_rejected(self):
  self.data['targets'][0]['uses']=['program'];self.save()
  with self.assertRaisesRegex(ValueError,'Cyclic'):clean_build.inspect(self.recipe)
 def test_nested_archive_rejected(self):
  self.data['targets'][0].update(kind='static',uses=['program']);self.save()
  with self.assertRaisesRegex(ValueError,'nested'):clean_build.inspect(self.recipe)
 def test_injected_flags_rejected(self):
  self.data['units'][0]['flags']=['-include','/tmp/injected.h'];self.save()
  with self.assertRaises(ValueError):clean_build.inspect(self.recipe)
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_real_build_and_overwrite_guard(self):
  out=Path(self.tmp.name)/'out'
  # Slot ownership belongs to the enclosing serial test run.
  r=clean_build.run(self.recipe,out,execute=True,slot_check=False)
  self.assertEqual(r['status'],'source_build_passed');self.assertTrue((out/'program').is_file())
  with self.assertRaises(ValueError):clean_build.run(self.recipe,out,execute=True,slot_check=False)

 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_recipe_changed_during_compile_rejected(self):
  import subprocess
  original=subprocess.run
  changed=False
  def compiler(*args,**kwargs):
   nonlocal changed
   result=original(*args,**kwargs)
   if '-c' in args[0] and not changed:
    changed=True
    self.data['note']='changed while compiler ran';self.save()
   return result
  with patch('clean_build.subprocess.run',side_effect=compiler):
   with self.assertRaisesRegex(RuntimeError,'recipe changed'):
    clean_build.run(self.recipe,Path(self.tmp.name)/'out',execute=True,slot_check=False)

 def test_bounded_unit_plan_does_not_link_or_accept_equivalence(self):
  r=clean_build.run(self.recipe,Path(self.tmp.name)/'out',only_units=['main'])
  self.assertEqual(r['selected_units'],['main']);self.assertEqual(r['targets'],[])
  with self.assertRaises(ValueError):clean_build.run(self.recipe,Path(self.tmp.name)/'out',only_units=['missing'])

 def test_dependency_classification_includes_system_marked_project_headers(self):
  header=self.root/'project.h';header.write_text('int value;')
  system=Path(self.tmp.name)/'sdk';system.mkdir();sdk=system/'sdk.h';sdk.write_text('int sdk;')
  with self.assertRaisesRegex(RuntimeError,'unstaged'):
   clean_build.classify_dependencies([header,sdk],self.root,self.data['files'],[system])
  self.data['files']['project.h']=clean_build.sha(header)
  project,toolchain=clean_build.classify_dependencies([header,sdk],self.root,self.data['files'],[system])
  self.assertEqual(project,['project.h']);self.assertEqual(toolchain,{str(sdk.resolve()):clean_build.sha(sdk)})
  external=Path(self.tmp.name)/'external.h';external.write_text('int external;')
  with self.assertRaisesRegex(RuntimeError,'unstaged'):
   clean_build.classify_dependencies([external],self.root,self.data['files'],[system])

 def test_bounded_build_records_full_dependencies_without_linking(self):
  import subprocess
  sdk=Path(self.tmp.name)/'sdk';sdk.mkdir();header=sdk/'toolchain.h';header.write_text('int sdk;')
  calls=[]
  def fake(args,**kwargs):
   calls.append(args)
   if '-E' in args:return subprocess.CompletedProcess(args,0,'','#include <...> search starts here:\n '+str(sdk)+'\nEnd of search list.\n')
   self.assertIn('-MD',args);self.assertNotIn('-MMD',args)
   self.assertEqual(kwargs['env']['TMPDIR'],str((Path(self.tmp.name)/'out/tmp').resolve()))
   self.assertTrue(Path(kwargs['env']['TMPDIR']).is_dir())
   Path(args[args.index('-o')+1]).write_bytes(b'isolated object fixture')
   Path(args[args.index('-MF')+1]).write_text('obj: '+str(self.root/'main.cpp')+' '+str(header)+'\n')
   return subprocess.CompletedProcess(args,0)
  with patch('clean_build.subprocess.run',side_effect=fake),patch('clean_build.subprocess.check_output',return_value='fixture compiler\n'):
   r=clean_build.run(self.recipe,Path(self.tmp.name)/'out',execute=True,slot_check=False,only_units=['main'])
  self.assertEqual(r['status'],'source_units_built');self.assertEqual(r['dependencies'],{'main':['main.cpp']})
  self.assertEqual(r['toolchain_headers'],{str(header.resolve()):clean_build.sha(header)})
  self.assertEqual(len([a for a in calls if '-c' in a]),1);self.assertFalse(r['equivalence_verified'])

 def test_receipt_and_case_collisions_cannot_overwrite_products(self):
  self.data['targets'][0]['id']='receipt.json';self.save()
  with self.assertRaisesRegex(ValueError,'Reserved'):clean_build.inspect(self.recipe)
  self.data['targets'][0]['id']='program'
  self.data['units'].append(dict(id='MAIN',source='main.cpp'));self.save()
  with self.assertRaisesRegex(ValueError,'duplicate unit'):clean_build.inspect(self.recipe)
  self.data['units'].pop();self.data['targets'].append(dict(id='PROGRAM',kind='static',units=['main']));self.save()
  with self.assertRaisesRegex(ValueError,'duplicate target'):clean_build.inspect(self.recipe)

 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_incremental_cache_requires_exact_source_and_object_receipts(self):
  first=Path(self.tmp.name)/'first'
  r=clean_build.run(self.recipe,first,execute=True,slot_check=False)
  self.data['targets'][0]['id']='renamed'
  revised=self.root/'recipe-v2.json';revised.write_text(json.dumps(self.data))
  second=Path(self.tmp.name)/'second'
  r=clean_build.run(revised,second,execute=True,slot_check=False,reuse_build=first,reuse_recipe=self.recipe)
  self.assertEqual(r['status'],'source_build_passed');self.assertEqual(r['fresh_compiled_units'],0)
  self.assertEqual(set(r['reused_verified_source_objects']),{'main'})
  self.assertFalse(r['historical_objects_used']);self.assertTrue(r['incremental_build'])
  self.assertTrue((second/'renamed').is_file());self.assertFalse((second/'obj/main.o').exists())
  (first/'obj/main.o').write_bytes(b'tampered')
  with self.assertRaisesRegex(ValueError,'object changed'):
   clean_build.run(revised,Path(self.tmp.name)/'third',execute=True,slot_check=False,reuse_build=first,reuse_recipe=self.recipe)

 def test_cache_rejects_incomplete_or_changed_toolchain_inventory(self):
  checked=clean_build.inspect(self.recipe);cache=Path(self.tmp.name)/'cache';cache.mkdir()
  record=dict(schema=1,status='running',historical_objects_used=False,recipe_sha256=clean_build.sha(self.recipe))
  (cache/'receipt.json').write_text(json.dumps(record))
  with self.assertRaisesRegex(ValueError,'has not finished'):
   clean_build.verified_source_cache(self.recipe,checked,cache,self.recipe,{},'compiler',[])
  record.update(status='failed',compiler_tools={},compiler='old',system_header_roots=[])
  (cache/'receipt.json').write_text(json.dumps(record))
  with self.assertRaisesRegex(ValueError,'toolchain differs'):
   clean_build.verified_source_cache(self.recipe,checked,cache,self.recipe,{},'new',[])

class TargetClosure(unittest.TestCase):
 def test_selected_runner_uses_only_its_transitive_source_units(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);src=p/'source';src.mkdir();files={}
   for n in ['main','library','unrelated']:
    (src/(n+'.cpp')).write_text('int '+n+'(){return 0;}\n');files[n+'.cpp']=clean_build.sha(src/(n+'.cpp'))
   recipe=src/'recipe.json';recipe.write_text(json.dumps(dict(schema=1,source_only=True,files=files,units=[dict(id=n,source=n+'.cpp') for n in ['main','library','unrelated']],targets=[dict(id='lib',kind='static',units=['library']),dict(id='runner',kind='executable',units=['main'],uses=['lib']),dict(id='other',kind='static',units=['unrelated'])])))
   r=clean_build.run(recipe,p/'out',only_targets=['runner'])
   self.assertEqual(r['selected_units'],['main','library']);self.assertEqual(r['targets'],['lib','runner']);self.assertFalse((p/'out').exists())
   with self.assertRaises(ValueError):clean_build.run(recipe,p/'out',only_targets=['absent'])
   with self.assertRaises(ValueError):clean_build.run(recipe,p/'out',only_targets=['runner'],only_units=['main'])

class DirectoryMetadata(unittest.TestCase):
 setUp=CleanBuild.setUp
 save=CleanBuild.save
 def test_valid_metadata_beside_a_pinned_source_directory_is_ignored(self):
  folder=self.root/'include/nested';folder.mkdir(parents=True);header=folder/'value.h';header.write_text('#define V 1\n')
  self.data['files']['include/nested/value.h']=clean_build.sha(header);self.data['units'][0]['includes']=['include'];self.save()
  side=folder.parent/'._nested';side.write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00metadata')
  clean_build.inspect(self.recipe)
  side.write_text('ordinary source')
  with self.assertRaisesRegex(ValueError,'Unpinned include'):clean_build.inspect(self.recipe)

class LinkFailureClassification(unittest.TestCase):
 def test_full_storage_is_distinct_from_a_source_link_failure(self):
  self.assertEqual(clean_build.failure_diagnostics('ranlib: No space left on device'),dict(kind='storage_exhausted'))

 def test_undefined_symbols_are_structured_not_silently_stubbed(self):
  result=clean_build.failure_diagnostics('Undefined symbols for architecture arm64:\n  "Service::find(unsigned int) const", referenced from:\n      caller in runtime.a\nld: symbol(s) not found for architecture arm64\n')
  self.assertEqual(result,dict(kind='source_symbol_closure_missing',undefined_symbols=['Service::find(unsigned int) const']))
  self.assertEqual(clean_build.failure_diagnostics("x.o: undefined reference to `foo()'")['undefined_symbols'],['foo()'])
 def test_duplicate_and_missing_dependency_classification(self):
  self.assertEqual(clean_build.failure_diagnostics('ld: duplicate symbol _foo')['kind'],'duplicate_source_providers')
  self.assertEqual(clean_build.failure_diagnostics('ld: framework not found Metal')['kind'],'missing_link_dependency')

class BuildCancellation(unittest.TestCase):
 setUp=CleanBuild.setUp
 save=CleanBuild.save
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_interrupt_cancels_pending_jobs_and_records_terminal_receipt(self):
  import subprocess
  from concurrent.futures import Future
  real=subprocess.run
  def interrupt(args,**kwargs):
   if '-c' in args:raise KeyboardInterrupt()
   return real(args,**kwargs)
  cancelled=[];original_cancel=Future.cancel
  def cancel(future):
   cancelled.append(future)
   return original_cancel(future)
  out=Path(self.tmp.name)/'interrupted'
  with patch('clean_build.subprocess.run',side_effect=interrupt),patch.object(Future,'cancel',cancel):
   with self.assertRaises(KeyboardInterrupt):clean_build.run(self.recipe,out,execute=True,slot_check=False)
  receipt=json.loads((out/'receipt.json').read_text())
  self.assertEqual(receipt['status'],'cancelled');self.assertTrue(cancelled)
  self.assertNotIn('artifacts',receipt)

class SourceContracts(unittest.TestCase):
 setUp=CleanBuild.setUp
 save=CleanBuild.save
 def test_source_contract_requires_implementation_and_linked_unit(self):
  self.data['source_contracts']=[dict(id='retained-fix',unit='main',target='program',required_fragments=['return 0;'])];self.save()
  clean_build.inspect(self.recipe)
  self.data['source_contracts'][0]['required_fragments']=['restore_required_service();'];self.save()
  with self.assertRaisesRegex(ValueError,'implementation missing'):clean_build.inspect(self.recipe)
  self.data['source_contracts'][0]['required_fragments']=['return 0;']
  self.data['units'].append(dict(id='other',source='main.cpp'))
  self.data['targets'][0]['units']=['other'];self.save()
  with self.assertRaisesRegex(ValueError,'not linked'):clean_build.inspect(self.recipe)
 def test_source_contract_rejects_ambiguous_or_malformed_obligations(self):
  self.data['source_contracts']=[dict(id='retained-fix',unit='main',target='program',required_fragments=['return 0;'])]*2;self.save()
  with self.assertRaisesRegex(ValueError,'duplicate source contract'):clean_build.inspect(self.recipe)
  self.data['source_contracts']=[dict(id='retained-fix',unit='main',target='program',required_fragments=[])];self.save()
  with self.assertRaisesRegex(ValueError,'bounded literal'):clean_build.inspect(self.recipe)

 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_contract_file_must_really_be_consumed_by_the_declared_unit(self):
  header=self.root/'service.hpp';header.write_text('constexpr int required_service=1;\n')
  self.data['files']['service.hpp']=clean_build.sha(header)
  self.data['source_contracts']=[dict(id='retained-service',unit='main',target='program',file='service.hpp',required_fragments=['required_service=1'])];self.save()
  clean_build.inspect(self.recipe)
  with self.assertRaisesRegex(RuntimeError,'not consumed'):
   clean_build.run(self.recipe,Path(self.tmp.name)/'unused',execute=True,slot_check=False)
  source=self.root/'main.cpp';source.write_text('#include "service.hpp"\nint main(){return required_service-1;}\n');self.data['files']['main.cpp']=clean_build.sha(source);self.save()
  result=clean_build.run(self.recipe,Path(self.tmp.name)/'used',execute=True,slot_check=False)
  self.assertEqual(result['status'],'source_build_passed')

class ObjectFormats(unittest.TestCase):
 setUp=CleanBuild.setUp
 save=CleanBuild.save
 def test_reusable_object_formats_exclude_linked_elf_artifacts(self):
  p=Path(self.tmp.name)/'candidate.o'
  for bits in (1,2):
   for endian in (1,2):
    for kind in (1,2,3):
     with self.subTest(bits=bits,endian=endian,kind=kind):
      h=bytearray(20);h[:4]=b'\x7fELF';h[4:7]=bytes([bits,endian,1])
      h[16:18]=kind.to_bytes(2,'little' if endian==1 else 'big')
      p.write_bytes(h);self.assertEqual(clean_build.object_binary(p),kind==1)
  for h in (b'\x7fELF',b'\x7fELF'+bytes(16),b'not an object'):
   p.write_bytes(h);self.assertFalse(clean_build.object_binary(p))
  p.write_bytes(b'BC\xc0\xde');self.assertTrue(clean_build.object_binary(p))
