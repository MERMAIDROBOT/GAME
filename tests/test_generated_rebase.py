from pathlib import Path
import gzip,hashlib,json,tempfile,unittest
from generated_rebase import rebase

class Rebase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old=self.root/'old';self.new=self.root/'new'
        (self.old/'bank').mkdir(parents=True);(self.new/'generated').mkdir(parents=True)
        (self.new/'disc-root').mkdir();(self.new/'disc-root/TEST.ELF').write_bytes(b'synthetic exact disc revision')
        self.pin=lambda raw:hashlib.sha256(raw).hexdigest()
        self.profile=dict(id='synthetic',expected_executable='TEST.ELF',expected_sha256=self.pin(b'synthetic exact disc revision'))
        self.base=['// source line %02d\n'%k for k in range(24)]
    def prepare(self,compiler_line=2,custom_line=20):
        fresh=self.base.copy();fresh[compiler_line]='// new compiler semantics\n'
        custom=self.base.copy();custom[custom_line]='// accepted title correction\n'
        b=''.join(self.base).encode();f=''.join(fresh).encode();c=''.join(custom).encode()
        with gzip.open(self.old/'base.cpp.gz','wb') as out:out.write(b)
        (self.old/'bank/sample.cpp').write_bytes(c);(self.new/'generated/sample.cpp').write_bytes(f)
        (self.new/'analysis').mkdir(exist_ok=True)
        (self.new/'analysis/translation-stage.json').write_text(json.dumps(dict(accepted=True,generated_sources={'sample.cpp':self.pin(f)})))
        manifest=dict(schema=1,kind='revision_generated_corrections',executable_sha256=self.profile['expected_sha256'],headers=[],
                      edits=[dict(file='sample.cpp',source='bank/sample.cpp',before_sha256=self.pin(b),after_sha256=self.pin(c),reason='Synthetic retained hook')])
        path=self.old/'bank/manifest.json';path.write_text(json.dumps(manifest));self.spec=dict(path='bank/manifest.json',sha256=self.pin(path.read_bytes()))
        return f
    def run_rebase(self):return rebase(self.profile,self.new,self.old,self.spec,{'sample.cpp':'base.cpp.gz'},'candidates/one')
    def test_clean_merge_preserves_both_updates_and_requires_explicit_activation(self):
        before=self.prepare();report=self.run_rebase()
        self.assertEqual(report['status'],'source_candidate');self.assertFalse(report['activated'])
        self.assertEqual((self.new/'generated/sample.cpp').read_bytes(),before)
        merged=(self.new/'candidates/one/sample.cpp').read_text()
        self.assertIn('new compiler semantics',merged);self.assertIn('accepted title correction',merged)
        from generated_corrections import apply
        applied=apply(dict(self.profile,generated_source_corrections=report['candidate_declaration']),self.new)
        self.assertEqual(applied['changed'],['sample.cpp'])
        self.assertEqual((self.new/'generated/sample.cpp').read_text(),merged)
    def test_conflict_never_creates_manifest_or_changes_live_sources(self):
        before=self.prepare(compiler_line=2,custom_line=2);report=self.run_rebase()
        self.assertEqual(report['status'],'conflicts');self.assertNotIn('candidate_declaration',report)
        self.assertFalse((self.new/'candidates/one/manifest.json').exists())
        self.assertEqual((self.new/'generated/sample.cpp').read_bytes(),before)
    def test_wrong_pinned_baseline_fails_before_writing_candidate(self):
        self.prepare()
        with gzip.open(self.old/'base.cpp.gz','wb') as out:out.write(b'incorrect baseline')
        with self.assertRaisesRegex(ValueError,'baseline pin changed'):self.run_rebase()
        self.assertFalse((self.new/'candidates').exists())

    def test_unrecorded_fresh_source_edits_are_rejected_before_candidate_output(self):
        self.prepare();(self.new/'generated/sample.cpp').write_text('// unexpected edit')
        with self.assertRaisesRegex(ValueError,'accepted translation receipt'):self.run_rebase()
        self.assertFalse((self.new/'candidates').exists())

    def test_default_base_lookup_uses_automatically_retained_pristine_bytes(self):
        self.prepare()
        from generated_corrections import retain_baselines
        baseline=''.join(self.base).encode();retain_baselines(self.old,{self.pin(baseline):baseline})
        report=rebase(self.profile,self.new,self.old,self.spec,None,'candidates/automatic')
        self.assertEqual(report['status'],'source_candidate')
