from pathlib import Path
import json,tempfile,unittest
from clean_build import sha
from archive_sources import audit
from archive_selection import select
from test_archive_sources import archive_member

class ArchiveSelection(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  self.src=self.p/'src';self.src.mkdir();(self.src/'a.cpp').write_text('int a;')
  self.recipe=self.src/'recipe.json';self.recipe.write_text(json.dumps(dict(schema=1,source_only=True,files={'a.cpp':sha(self.src/'a.cpp')},units=[dict(id='a',source='a.cpp'),dict(id='b',source='a.cpp')],targets=[dict(id='lib',kind='static',units=['a','b'])])))
  self.archive=self.p/'old.a';self.archive.write_bytes(b'!<arch>\n'+archive_member('a.o',b'reference'))
  self.audit=self.p/'audit.json';self.audit.write_text(json.dumps(audit(self.archive,[])))
  self.build=self.p/'build';(self.build/'obj').mkdir(parents=True)
  for name in ['a','b']:(self.build/'obj'/(name+'.o')).write_bytes(b'reference')
  self.receipt=self.build/'receipt.json';self.receipt.write_text(json.dumps(dict(status='source_build_passed',recipe_sha256=sha(self.recipe),objects={name:sha(self.build/'obj'/(name+'.o')) for name in ['a','b']})))
  self.candidates=self.p/'candidates.json';self.candidates.write_text(json.dumps(dict(schema=1,candidates=[dict(ordinal=0,unit='a')])))
  self.controls=self.p/'controls.json';self.rows=[dict(unit=name,object_sha256=sha(self.build/'obj'/(name+'.o')),reference_sha256=sha(self.build/'obj/a.o'),exact_object_bytes=True,native_text_match=None) for name in ['a','b']];self.save()
 def save(self):self.controls.write_text(json.dumps(dict(status='controls_match',recipe_sha256=sha(self.recipe),build_receipt_sha256=sha(self.receipt),native_text_requested=True,objects=self.rows)))
 def run_select(self):return select(self.audit,self.recipe,self.build,self.controls,self.candidates,self.p/'out.json')
 def test_complete_selection_does_not_claim_gameplay_or_full_equivalence(self):
  r=self.run_select();self.assertEqual(r['selected_units'],['a']);self.assertFalse(r['equivalence_verified']);self.assertFalse(r['historical_objects_linked'])
 def test_multiple_matches_are_blocked(self):
  self.candidates.write_text(json.dumps(dict(schema=1,candidates=[dict(ordinal=0,unit=n) for n in ['a','b']])))
  r=self.run_select();self.assertEqual(r['unresolved'][0]['reason'],'multiple_matching_sources');self.assertFalse(r['complete_member_coverage'])
 def test_text_match_remains_weaker_than_full_object(self):
  obj=self.build/'obj/a.o';obj.write_bytes(b'different metadata');r=json.loads(self.receipt.read_text());r['objects']['a']=sha(obj);self.receipt.write_text(json.dumps(r))
  self.rows[0].update(object_sha256=sha(obj),exact_object_bytes=False,native_text_match=True,built_text_sha256='f'*64,reference_text_sha256='f'*64,built_text_bytes=4,reference_text_bytes=4);self.save()
  r=self.run_select();self.assertEqual(r['selected'][0]['strength'],'native_text_only');self.assertFalse(r['equivalence_verified'])
 def test_changed_object_and_wrong_archive_reference_rejected(self):
  self.rows[0]['reference_sha256']='a'*64;self.save()
  with self.assertRaisesRegex(ValueError,'archive member'):self.run_select()
  self.rows[0]['reference_sha256']=sha(self.build/'obj/a.o');self.save();(self.build/'obj/a.o').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'changed'):self.run_select()
 def test_forged_exact_match_and_overwrite_rejected(self):
  self.rows[0]['exact_object_bytes']=False;self.save()
  with self.assertRaisesRegex(ValueError,'Inconsistent'):self.run_select()
  self.rows[0]['exact_object_bytes']=True;self.save();self.run_select()
  with self.assertRaisesRegex(ValueError,'Preserve'):self.run_select()
 def test_stale_control_report_rejected(self):
  x=json.loads(self.controls.read_text());x['build_receipt_sha256']='0'*64;self.controls.write_text(json.dumps(x))
  with self.assertRaisesRegex(ValueError,'stale'):self.run_select()
