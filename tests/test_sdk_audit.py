from pathlib import Path
import json,re,tempfile,unittest
from sdk_policy import audit_generated,REQUIRES_BODY
ROOT=Path(__file__).resolve().parents[1]
class SdkAudit(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name);self.generated=self.p/'generated';self.generated.mkdir()
 def test_stateful_substitutions_are_rejected_without_changing_sources(self):
  p=self.generated/'remote.cpp';text='void f(){ps2_stubs :: sceSdRemote(a,b,c);}\n';p.write_text(text)
  report=audit_generated(self.generated);self.assertFalse(report['passed']);self.assertEqual(report['findings'][0]['symbol'],'sceSdRemote');self.assertEqual(p.read_text(),text)
 def test_comments_strings_and_other_handlers_do_not_trigger_policy(self):
  (self.generated/'remote.cpp').write_text('// ps2_stubs::sceSdRemote(a,b,c);\nconst char *s="ps2_stubs::sceSdRemoteInit(a,b,c)";\nvoid f(){ps2_stubs::scePrintf(a,b,c);}\n')
  self.assertTrue(audit_generated(self.generated)['passed'])
 def test_compiler_selector_and_output_gate_share_the_policy(self):
  header=(ROOT/'runtime/abi/sdk_policy.h').read_text();self.assertEqual(set(re.findall(r'name == "([A-Za-z0-9_]+)"',header)),REQUIRES_BODY)
 def test_old_accepted_translation_cannot_bypass_new_sdk_gate(self):
  import pipeline
  (self.p/'analysis').mkdir();(self.p/'analysis/translation-stage.json').write_text(json.dumps(dict(accepted=True)))
  (self.generated/'remote.cpp').write_text('void f(){ps2_stubs::sceSdRemoteInit(a,b,c);}')
  with self.assertRaisesRegex(ValueError,'incomplete stateful SDK'):pipeline.require_accepted_translation(self.p)
