from pathlib import Path
import json,tempfile,unittest
from build_closure import audit
class Closure(unittest.TestCase):
 def test_exact_object_mapping_and_archive_blocker(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'a.cpp').write_text('int a;');db=p/'db.json'
   db.write_text(json.dumps([{'directory':t,'file':'a.cpp','arguments':['clang++','-c','a.cpp','-o','a.o']}]))
   r=audit(['clang++','a.o','old.a','-o','runner'],db,p)
   self.assertEqual(r['source_mapped'],1);self.assertEqual(r['blockers'],1);self.assertFalse(r['clean_rebuild_ready'])
   r=audit(['clang++','elsewhere/a.o','-o','runner'],db,p)
   self.assertEqual(r['source_mapped'],0) # Same basename is insufficient.
 def test_unresolved_libraries_and_wrappers_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);db=p/'db.json';db.write_text('[]')
   self.assertEqual(audit(['clang++','-lcustom','-o','x'],db,p)['blockers'],1)
   for cmd in [['python3','link.py','x.o'],['clang++','@response']]:
    with self.assertRaises(ValueError):audit(cmd,db,p)

 def test_version_suffixed_bitcode_is_inventoried_and_source_candidate_found(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'obj').mkdir();source=p/'unity_293.cxx';source.write_text('int a;');obj=p/'obj/unity_293.cxx.o.v46';obj.write_bytes(b'\xde\xc0\x17\x0bfixture');db=p/'db.json';db.write_text('[]')
   r=audit(['clang++',str(obj),'-o','runner'],db,p)
   self.assertEqual(len(r['inputs']),1);self.assertEqual(r['blockers'],1)
   self.assertEqual(r['inputs'][0]['source_candidates'][0]['source'],str(source.resolve()))
   self.assertFalse(r['clean_rebuild_ready'])
