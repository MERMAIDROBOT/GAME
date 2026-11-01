from pathlib import Path
import json,tempfile,unittest
from clean_build import sha
from link_recipe import recover
class LinkRecipe(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  self.obj=self.p/'old.o';self.obj.write_bytes(b'old object');self.lib=self.p/'old.a';self.lib.write_bytes(b'old library');self.spec=self.p/'mappings.json'
  self.spec.write_text(json.dumps(dict(schema=1,products=[dict(input=str(self.obj.resolve()),sha256=sha(self.obj),unit='translated.main'),dict(input=str(self.lib.resolve()),sha256=sha(self.lib),target='runtime.source')])) )
 def parse(self,args):return recover(['clang++',*args],self.p,self.spec,'game')
 def test_recorded_optimization_frameworks_and_source_order_are_preserved(self):
  r=self.parse(['-O3','-DNDEBUG','-flto=thin','old.o','old.a','-framework','Cocoa','-pthread','-o','old-runner'])
  self.assertEqual(r['status'],'source_link_target_recovered');t=r['target'];self.assertEqual(t['units'],['translated.main']);self.assertEqual(t['uses'],['runtime.source']);self.assertIn('-flto=thin',t['link_flags']);self.assertFalse(r['historical_objects_linked'])
 def test_historical_lto_cache_is_dropped_without_loading_it(self):
  r=self.parse(['-Xlinker','-cache_path_lto','-Xlinker','/missing/old-cache','old.o','-o','old-runner'])
  self.assertEqual(r['status'],'source_link_target_recovered');self.assertIn('Historical LTO cache',r['ignored'][0]['reason'])
 def test_unmapped_input_and_unknown_linker_commands_block_target(self):
  for args in [['old.o','unmapped.a','-o','game'],['old.o','-Xlinker','-load','evil.dylib','-o','game'],['old.o','-lunknown','-o','game']]:
   r=self.parse(args);self.assertIsNone(r['target']);self.assertEqual(r['status'],'source_link_target_blocked')
 def test_changed_identity_control_is_rejected(self):
  self.obj.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'mapping pin'):self.parse(['old.o','-o','game'])
 def test_shared_install_name_and_rpath_are_kept_bundle_relative(self):
  r=self.parse(['-dynamiclib','old.o','-install_name','@rpath/libgame.dylib','-Wl,-rpath,@loader_path','-o','old.dylib'])
  self.assertEqual(r['target']['kind'],'shared');self.assertEqual(r['target']['rpaths'],['@loader_path'])
  self.assertIsNone(self.parse(['old.o','-Wl,-rpath,/old/machine','-o','game'])['target'])
 def test_direct_objects_after_archive_are_not_silently_reordered(self):
  r=self.parse(['old.a','old.o','-o','game']);self.assertIsNone(r['target']);self.assertIn('archive boundary',r['blocked'][0]['error'])

 def test_explicit_versioned_object_mapping_and_sdk_alias_are_supported(self):
  versioned=self.p/'old.o.v46';versioned.write_bytes(self.obj.read_bytes());data=json.loads(self.spec.read_text());data['products'][0]['input']=str(versioned.resolve());self.spec.write_text(json.dumps(data))
  sdk=self.p/'MacOSX.test.sdk/usr/lib';sdk.mkdir(parents=True);(sdk/'libSystem.B.tbd').write_text('!tapi-tbd');(sdk/'libm.tbd').symlink_to(sdk/'libSystem.B.tbd')
  r=self.parse(['old.o.v46',str(sdk/'libm.tbd'),'-o','game'])
  self.assertEqual(r['status'],'source_link_target_recovered');self.assertEqual(r['target']['libraries'],['m']);self.assertEqual(len(r['sdk_inputs']),1)
