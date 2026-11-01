from pathlib import Path
import json,shutil,tempfile,unittest
from clean_build import inspect,sha,verified_source_cache

class SourceReuse(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.old=self.root/'old';self.old.mkdir()
  for name,text in [('main.cpp','#include "used.h"\nint main(){return VALUE;}\n'),('used.h','#define VALUE 0\n'),('unused.h','// pinned but unused\n')]: (self.old/name).write_text(text)
  self.recipe=dict(schema=1,source_only=True,files={n:sha(self.old/n) for n in ['main.cpp','used.h','unused.h']},units=[dict(id='main',source='main.cpp')],targets=[dict(id='runner',kind='executable',units=['main'])])
  self.old_recipe=self.old/'recipe.json';self.old_recipe.write_text(json.dumps(self.recipe));self.new=self.root/'new';shutil.copytree(self.old,self.new)
  self.new_recipe=self.new/'recipe.json';self.cache=self.root/'cache';(self.cache/'obj').mkdir(parents=True);self.obj=self.cache/'obj/main.o';self.obj.write_bytes(b'nonexecuted synthetic object')
  self.record=dict(schema=1,historical_objects_used=False,recipe_sha256=sha(self.old_recipe),status='source_build_passed',compiler_tools={},compiler='test compiler',system_header_roots=[],toolchain_headers={},objects=dict(main=sha(self.obj)),dependencies=dict(main=['main.cpp','used.h']))
  self.save_cache()
 def save_cache(self):(self.cache/'receipt.json').write_text(json.dumps(self.record))
 def check(self):return verified_source_cache(self.new_recipe,inspect(self.new_recipe),self.cache,self.old_recipe,{},'test compiler',[])[0]
 def change(self,name,text):
  (self.new/name).write_text(text);r=json.loads(self.new_recipe.read_text());r['files'][name]=sha(self.new/name);self.new_recipe.write_text(json.dumps(r))
 def test_relocated_identical_units_reuse_verified_objects(self):self.assertEqual(set(self.check()),{'main'})
 def test_changed_dependency_is_recompiled(self):self.change('used.h','#define VALUE 1\n');self.assertEqual(self.check(),{})
 def test_changed_source_is_recompiled(self):self.change('main.cpp','int main(){return 1;}\n');self.assertEqual(self.check(),{})
 def test_unused_header_content_does_not_invalidate_unrelated_object(self):self.change('unused.h','// changed but no include resolution changes\n');self.assertEqual(set(self.check()),{'main'})
 def test_new_file_can_change_include_resolution_so_blocks_cache(self):
  self.change('new.h','// new path\n')
  with self.assertRaisesRegex(ValueError,'inventory paths'):self.check()
 def test_changed_compile_settings_are_not_reused(self):
  r=json.loads(self.new_recipe.read_text());r['units'][0]['defines']=['OTHER=1'];self.new_recipe.write_text(json.dumps(r));self.assertEqual(self.check(),{})
 def test_object_tampering_is_rejected(self):
  self.obj.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'object changed'):self.check()
 def test_incomplete_dependency_record_is_rejected(self):
  self.record['dependencies']['main']=['used.h'];self.save_cache()
  with self.assertRaisesRegex(ValueError,'closure missing'):self.check()

 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_real_relocated_edit_recompiles_changed_unit_and_runs_new_behavior(self):
  import subprocess,clean_build
  (self.old/'helper.cpp').write_text('int helper(){return 7;}\n')
  self.recipe['files']['helper.cpp']=sha(self.old/'helper.cpp');self.recipe['units'].append(dict(id='helper',source='helper.cpp'));self.recipe['targets'][0]['units'].append('helper');self.old_recipe.write_text(json.dumps(self.recipe))
  before=clean_build.run(self.old_recipe,self.root/'before',execute=True,slot_check=False)
  shutil.rmtree(self.new);shutil.copytree(self.old,self.new);self.change('used.h','#define VALUE 1\n')
  after=clean_build.run(self.new_recipe,self.root/'after',execute=True,slot_check=False,reuse_build=self.root/'before',reuse_recipe=self.old_recipe)
  self.assertEqual(after['fresh_compiled_units'],1);self.assertEqual(set(after['reused_verified_source_objects']),{'helper'})
  self.assertEqual(subprocess.run([after['artifacts']['runner']['path']]).returncode,1)
  completed=clean_build.verify_completed_build(self.new_recipe,self.root/'after',sha(self.root/'after/receipt.json'),self.root,['runner'])
  self.assertTrue(completed['completed_build_verified']);self.assertEqual(completed['artifacts'],after['artifacts'])
  selected=clean_build.direct_source_cache(self.root/'after',self.new_recipe,self.root,receipt_sha256=sha(self.root/'after/receipt.json'),recipe_sha256=sha(self.new_recipe))
  self.assertEqual(selected['build'],self.root/'before');self.assertEqual(len(selected['hops']),2)
  third=clean_build.run(self.new_recipe,self.root/'third',execute=True,slot_check=False,reuse_build=selected['build'],reuse_recipe=selected['recipe'])
  self.assertEqual(third['fresh_compiled_units'],1);self.assertEqual(set(third['reused_verified_source_objects']),{'helper'})
  self.assertEqual(subprocess.run([third['artifacts']['runner']['path']]).returncode,1)
  (self.root/'after/runner').write_bytes(b'changed linked output')
  with self.assertRaisesRegex(ValueError,'artifact changed'):clean_build.verify_completed_build(self.new_recipe,self.root/'after',sha(self.root/'after/receipt.json'),self.root,['runner'])

class CacheAncestorSelection(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  self.base=self.root/'base';self.base.mkdir();self.recipe=self.root/'base-recipe.json';self.recipe.write_text('{}')
  self.record=dict(schema=1,status='source_build_passed',historical_objects_used=False,recipe_sha256=sha(self.recipe),objects={})
  self.save_base()
 def save_base(self):(self.base/'receipt.json').write_text(json.dumps(self.record))
 def select(self,build=None,recipe=None):
  from clean_build import direct_source_cache
  build=build or self.base;recipe=recipe or self.recipe
  return direct_source_cache(build,recipe,self.root,receipt_sha256=sha(build/'receipt.json'),recipe_sha256=sha(recipe))
 def child(self,parent=None):
  child=self.root/'incremental';child.mkdir();recipe=self.root/'child-recipe.json';recipe.write_text('{"next":true}')
  upstream=parent or dict(path=str(self.base/'receipt.json'),sha256=sha(self.base/'receipt.json'),recipe=str(self.recipe),recipe_sha256=sha(self.recipe))
  record=dict(self.record,recipe_sha256=sha(recipe),reused_verified_source_objects={'old':'not selected'},source_cache_receipt=upstream)
  (child/'receipt.json').write_text(json.dumps(record));return child,recipe
 def test_direct_receipt_selected_without_claiming_fresh_current_output(self):
  result=self.select();self.assertEqual(result['build'],self.base);self.assertEqual(len(result['hops']),1)
 def test_incremental_receipt_selects_only_pinned_direct_ancestor(self):
  child,recipe=self.child();result=self.select(child,recipe)
  self.assertEqual(result['build'],self.base);self.assertEqual(result['recipe'],self.recipe);self.assertEqual(len(result['hops']),2)
  self.assertNotEqual(result['build'],child)
 def test_changed_parent_receipt_or_recipe_rejected(self):
  child,recipe=self.child();self.record['unexpected']='changed';self.save_base()
  with self.assertRaisesRegex(ValueError,'pin changed'):self.select(child,recipe)
 def test_parent_outside_private_storage_rejected_before_read(self):
  child,recipe=self.child(dict(path='/outside/receipt.json',sha256='a'*64,recipe='/outside/recipe.json',recipe_sha256='b'*64))
  with self.assertRaisesRegex(ValueError,'escapes private'):self.select(child,recipe)
 def test_missing_ancestry_and_historical_receipts_rejected(self):
  self.record['reused_verified_source_objects']={'old':{}};self.save_base()
  with self.assertRaisesRegex(ValueError,'no pinned'):self.select()
  self.record['historical_objects_used']=True;self.save_base()
  with self.assertRaisesRegex(ValueError,'finished source build'):self.select()
 def test_symlinked_ancestor_rejected(self):
  linked=self.root/'alias';linked.symlink_to(self.base,target_is_directory=True)
  from clean_build import direct_source_cache
  with self.assertRaisesRegex(ValueError,'symlink'):
   direct_source_cache(linked,self.recipe,self.root,receipt_sha256=sha(self.base/'receipt.json'),recipe_sha256=sha(self.recipe))

 def test_malformed_parent_path_rejected(self):
  child,recipe=self.child(dict(path=None,sha256='a'*64,recipe=str(self.recipe),recipe_sha256=sha(self.recipe)))
  with self.assertRaisesRegex(ValueError,'valid path'):self.select(child,recipe)
 def test_nonobject_receipt_rejected(self):
  (self.base/'receipt.json').write_text('[]')
  with self.assertRaisesRegex(ValueError,'must be an object'):self.select()
