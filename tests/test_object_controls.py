from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from clean_build import sha
from object_controls import compare
class ObjectControls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  self.src=self.p/'source';self.src.mkdir();(self.src/'a.cpp').write_text('int a;')
  self.recipe=self.src/'recipe.json';self.recipe.write_text(json.dumps(dict(schema=1,source_only=True,files={'a.cpp':sha(self.src/'a.cpp')},units=[dict(id='a',source='a.cpp')],targets=[dict(id='lib',kind='static',units=['a'])])))
  self.build=self.p/'build';(self.build/'obj').mkdir(parents=True);self.obj=self.build/'obj/a.o';self.obj.write_bytes(b'object fixture')
  self.receipt=self.build/'receipt.json';self.receipt.write_text(json.dumps(dict(status='source_units_built',recipe_sha256=sha(self.recipe),objects={'a':sha(self.obj)})))
  self.ref=self.p/'reference.o';self.ref.write_bytes(self.obj.read_bytes());self.refs=self.p/'refs.json';self.save()
 def save(self):self.refs.write_text(json.dumps(dict(schema=1,objects=[dict(unit='a',reference=str(self.ref),sha256=sha(self.ref))])))
 def test_exact_object_control_still_does_not_certify_gameplay_or_full_equivalence(self):
  r=compare(self.recipe,self.build,self.refs,self.p/'out');self.assertTrue(r['all_exact_object_bytes']);self.assertIsNone(r['all_native_text_match']);self.assertFalse(r['equivalence_verified'])
 def test_native_text_is_only_a_separate_weaker_control(self):
  self.ref.write_bytes(b'different metadata');self.save()
  with patch('source_freeze.native_text',return_value=b'identical machine text'):
   r=compare(self.recipe,self.build,self.refs,self.p/'out',native=True,slot_check=False)
  self.assertFalse(r['all_exact_object_bytes']);self.assertTrue(r['all_native_text_match']);self.assertFalse(r['equivalence_verified'])
 def test_modified_build_and_self_reference_rejected(self):
  self.obj.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'pin'):compare(self.recipe,self.build,self.refs,self.p/'out')
  self.ref=self.obj;self.save()
  with self.assertRaisesRegex(ValueError,'own independent'):compare(self.recipe,self.build,self.refs,self.p/'out')
 def test_running_build_cannot_be_a_passed_control(self):
  r=json.loads(self.receipt.read_text());r['status']='running';self.receipt.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'completed'):compare(self.recipe,self.build,self.refs,self.p/'out')

 def test_version_suffix_requires_real_object_format_and_still_only_compares_bytes(self):
  self.ref=self.p/'reference.o.v46';self.ref.write_bytes(b'\xde\xc0\x17\x0bfixture');self.obj.write_bytes(self.ref.read_bytes())
  r=json.loads(self.receipt.read_text());r['objects']['a']=sha(self.obj);self.receipt.write_text(json.dumps(r));self.save()
  result=compare(self.recipe,self.build,self.refs,self.p/'out');self.assertTrue(result['all_exact_object_bytes']);self.assertFalse(result['equivalence_verified'])
