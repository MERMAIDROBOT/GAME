from pathlib import Path
import json,tempfile,unittest
from compile_recipe import settings,recover
from source_graph import stage
from clean_build import inspect

class CompileRecipe(unittest.TestCase):
 def entry(self,args):return dict(directory='/tmp/compiler-project',arguments=['/usr/bin/clang++',*args])
 def test_preserves_search_order_and_compiler_semantics(self):
  r=settings(self.entry(['-I','first','-isystem','second','-iquote','third','-DCOUNT=2','-std=gnu++20','-O3','-flto=thin','-include','forced.h','-o','obj.o','-c','source.cpp']))
  self.assertEqual([x['kind'] for x in r['include_paths']],['-I','-isystem','-iquote'])
  self.assertEqual(r['force_include'],[str(Path('/tmp/compiler-project/forced.h').resolve())])
  self.assertEqual(r['flags'],['-O3','-flto=thin']);self.assertEqual(r['defines'],['COUNT=2'])
 def test_injection_link_inputs_and_pch_rejected(self):
  for arg in ['@response','-Xclang','-include-pch','-fplugin=x','libold.a',';','$(touch /tmp/unsafe)']:
   with self.subTest(arg=arg),self.assertRaises(ValueError):settings(self.entry([arg,'-c','a.cpp','-o','a.o']))
 def test_database_mismatch_and_unknown_driver_rejected(self):
  e=self.entry(['-c','a.cpp','-o','a.o']);e['file']='wrong.cpp'
  with self.assertRaisesRegex(ValueError,'disagrees'):settings(e)
  e=self.entry(['-c','a.cpp','-o','a.o']);e['arguments'][0]='/tmp/custom-wrapper'
  with self.assertRaisesRegex(ValueError,'driver'):settings(e)
 def test_sdk_search_reconstructed_but_non_sdk_frameworks_rejected(self):
  r=settings(self.entry(['-F/Library/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks','-c','a.cpp','-o','a.o']))
  self.assertTrue(r['sdk_framework_search'])
  with self.assertRaisesRegex(ValueError,'SDK'):settings(self.entry(['-F/private/build/Frameworks','-c','a.cpp','-o','a.o']))
 def test_recover_records_blockers_instead_of_dropping_unknown_flags(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'db.json';p.write_text(json.dumps([self.entry(['-c','a.cpp','-o','a.o']),self.entry(['-unknown','-c','b.cpp','-o','b.o'])]))
   r=recover(p);self.assertEqual(len(r['accepted']),1);self.assertEqual(len(r['blocked']),1);self.assertFalse(r['recorded_settings_recovered'])
 def test_ordered_paths_survive_staging(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);root=p/'src';root.mkdir();(root/'value.h').write_text('#define VALUE 1\n');(root/'a.cpp').write_text('int a(){return 1;}\n')
   unit=settings(dict(directory=str(root),arguments=['clang++','-std=c++20','-isystem',str(root),'-iquote',str(root),'-c','a.cpp','-o','a.o']))
   unit.pop('output');unit['id']='a'
   config=p/'config.json';config.write_text(json.dumps(dict(schema=1,roots=dict(core=str(root)),units=[unit],targets=[dict(id='a',kind='static',units=['a'])])))
   stage(config,p/'stage');r=inspect(p/'stage/recipe.json')
   self.assertEqual(r['recipe']['units'][0]['include_paths'],[dict(kind='-isystem',path='sources/core'),dict(kind='-iquote',path='sources/core')])

 def test_cmake_pch_pair_rebuilt_from_pinned_source_only(self):
  with tempfile.TemporaryDirectory() as d:
   h=Path(d)/'cmake_pch.hxx';h.write_text('#include "runtime.h"\n')
   args=['-std=c++20','-Xclang','-include-pch','-Xclang',str(h)+'.pch','-Xclang','-include','-Xclang',str(h),'-c','a.cpp','-o','a.o']
   e=self.entry(args)
   with self.assertRaises(ValueError):settings(e)
   r=settings(e,source_pch=True)
   self.assertEqual(r['force_include'],[str(h.resolve())]);self.assertFalse(r['pch_reconstructed'][0]['historical_cache_used'])
   e=self.entry([x.replace('cmake_pch.hxx.pch','different.pch') for x in args])
   with self.assertRaisesRegex(ValueError,'matching'):settings(e,source_pch=True)
   e=self.entry(['-Xclang','-load','-Xclang','evil.dylib','-c','a.cpp','-o','a.o'])
   with self.assertRaises(ValueError):settings(e,source_pch=True)
