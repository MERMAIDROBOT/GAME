from pathlib import Path
import tempfile,unittest
from make_plan import recover
from compile_recipe import settings

class MakePlan(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name);self.transcript=self.p/'plan.txt'
 def plan(self,text):
  self.transcript.write_text(text);return recover(self.transcript,self.p)
 def test_compile_mode_source_last_and_dependency_outputs_reconstructed(self):
  r=self.plan('mkdir -p lib\nclang -I. -std=c17 -MMD -MF lib/a.d -MT lib/a.o -c -o lib/a.o a.c\nar rc lib/test.a lib/a.o\nranlib lib/test.a\n')
  self.assertEqual(r['status'],'make_plan_recovered');self.assertEqual(r['units'][0]['source'],str((self.p/'a.c').resolve()));self.assertEqual(r['targets'][0]['units'],['unit_0']);self.assertEqual(len(r['commands_not_executed']),2)
  self.assertNotIn('-MMD',r['units'][0]['flags']);self.assertFalse(r['equivalence_verified'])
 def test_shell_expression_never_executed(self):
  r=self.plan('clang -c a.c -o a.o ; touch marker\nar rc test.a a.o\n')
  self.assertEqual(r['status'],'make_plan_blocked');self.assertFalse((self.p/'marker').exists())
 def test_old_archive_input_without_source_compile_is_blocked(self):
  r=self.plan('clang -c a.c -o a.o\nar rc test.a a.o old.o\n')
  self.assertTrue(any('without a recovered source' in x['error'] for x in r['blocked']))
 def test_duplicate_outputs_and_unknown_compiler_flags_are_blocked(self):
  for text in ['clang -c a.c -o a.o\nclang -c b.c -o a.o\nar rc test.a a.o\n','clang -fplugin=evil -c a.c -o a.o\nar rc test.a a.o\n']:
   self.assertEqual(self.plan(text)['status'],'make_plan_blocked')
 def test_make_style_source_last_does_not_accept_multiple_sources(self):
  with self.assertRaises(ValueError):settings(dict(directory=str(self.p),arguments=['clang','-c','-o','a.o','a.c','b.c']))
 def test_partial_archive_without_compiler_or_archive_commands_is_blocked(self):
  self.assertEqual(self.plan('touch file\n')['status'],'make_plan_blocked')
