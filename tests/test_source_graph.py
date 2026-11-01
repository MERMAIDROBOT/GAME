from pathlib import Path
import json,tempfile,unittest,subprocess,sys
from source_graph import stage,relocate
from clean_build import sha
class SourceGraph(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  self.src=self.p/'original';self.src.mkdir();(self.src/'value.h').write_text('#define VALUE 7\n')
  (self.src/'main.cpp').write_text('#include "'+str(self.src/'value.h')+'"\nint value(){return VALUE;}\n')
  self.data={'schema':1,'roots':{'core':str(self.src)},'units':[{'id':'main','source':str(self.src/'main.cpp'),'include_roots':['core']}],'targets':[{'id':'core','kind':'static','units':['main']}]}
  self.config=self.p/'config.json'
 def run_stage(self):
  self.config.write_text(json.dumps(self.data));return stage(self.config,self.p/'staged')
 def test_relocation_preserves_recipe_ledger_and_omits_unlisted_binaries(self):
  self.run_stage();root=self.p/'staged';(root/'historical.o').write_bytes(b'not copied')
  before=(sha(root/'recipe.json'),sha(root/'staging.json'))
  result=relocate(root/'recipe.json',root/'staging.json',self.p/'relocated')
  self.assertEqual((sha(self.p/'relocated/recipe.json'),sha(self.p/'relocated/staging.json')),before)
  self.assertFalse((self.p/'relocated/historical.o').exists());self.assertFalse(result['historical_objects_copied'])
  self.assertFalse(result['gameplay_verified']);self.assertEqual(result['status'],'source_graph_relocated_not_built')
 def test_relocation_refuses_changed_source_or_incomplete_ledger(self):
  self.run_stage();root=self.p/'staged';ledger=root/'staging.json';data=json.loads(ledger.read_text());data['files']=[];ledger.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'every source input'):relocate(root/'recipe.json',ledger,self.p/'out')
  self.assertFalse((self.p/'out').exists())
 def test_relocation_cannot_overwrite_input_or_existing_directory(self):
  self.run_stage();root=self.p/'staged'
  for destination in [root,root/'nested',self.p]:
   with self.assertRaisesRegex(ValueError,'fresh nonoverlapping'):relocate(root/'recipe.json',root/'staging.json',destination)
 def test_cpp_cli_relocates_source_without_a_game_profile_or_compiler(self):
  self.run_stage();root=self.p/'staged';entry=Path(__file__).resolve().parents[1]/'run.py'
  result=subprocess.run([sys.executable,str(entry),'relocate-source-graph',str(root/'recipe.json'),'--staging',str(root/'staging.json'),'--output',str(self.p/'cli-copy')],capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'source_graph_relocated_not_built')
 def test_relocatable_snapshot_and_no_historical_objects(self):
  (self.src/'old.o').write_bytes(b'not source')
  r=self.run_stage();self.assertEqual(r['source_files'],2);self.assertFalse(r['equivalence_verified'])
  self.assertIn('#include "value.h"',(self.p/'staged/sources/core/main.cpp').read_text())
  self.assertFalse((self.p/'staged/sources/core/old.o').exists())
  with self.assertRaises(ValueError):stage(self.config,self.p/'staged')
 def test_unknown_absolute_include_rejected(self):
  (self.src/'main.cpp').write_text('#include "/outside/project.h"\n')
  with self.assertRaisesRegex(ValueError,'outside declared'):self.run_stage()
 def test_unknown_include_root_rejected(self):
  self.data['units'][0]['include_roots']=['missing']
  with self.assertRaisesRegex(ValueError,'Unknown include'):self.run_stage()
 def test_symlink_rejected(self):
  (self.src/'link.h').symlink_to(self.src/'value.h')
  with self.assertRaisesRegex(ValueError,'symlink'):self.run_stage()
 def test_nested_roots_preserve_overlay_and_parent_include(self):
  sub=self.src/'nested';sub.mkdir();(sub/'inner.h').write_text('#include "../value.h"\n')
  self.data['roots']['overlay']=str(sub)
  r=self.run_stage();self.assertEqual(r['source_files'],4)
  self.assertIn('../core/value.h',(self.p/'staged/sources/overlay/inner.h').read_text())

 def test_nested_root_keeps_parent_include_search_topology(self):
  sub=self.src/'Stubs';sub.mkdir();(sub/'inner.h').write_text('#define INNER 9\n')
  (self.src/'main.cpp').write_text('#include "Stubs/inner.h"\nint value(){return INNER;}\n')
  self.data['roots']['stubs']=str(sub)
  self.run_stage()
  root=self.p/'staged'
  self.assertEqual((root/'sources/core/Stubs/inner.h').read_bytes(),(root/'sources/stubs/inner.h').read_bytes())
  # Remove the original tree: the staged compiler search must stand alone.
  import shutil,subprocess
  shutil.rmtree(self.src)
  result=subprocess.run(['clang++','-fsyntax-only','-I',str(root/'sources/core'),str(root/'sources/core/main.cpp')],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)

 def test_valid_appledouble_sidecars_are_not_staged_as_source_headers(self):
  (self.src/'._value.h').write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00'+b'metadata')
  report=self.run_stage();self.assertEqual(report['source_files'],2)
  self.assertFalse((self.p/'staged/sources/core/._value.h').exists())

 def test_non_utf8_source_bytes_and_line_endings_preserved(self):
  raw=b'// temperature 20\xb0C\r\nint a=1;\r\n'
  (self.src/'legacy.c').write_bytes(raw)
  self.run_stage()
  self.assertEqual((self.p/'staged/sources/core/legacy.c').read_bytes(),raw)

 def test_relocated_parent_include_uses_recorded_quote_search(self):
  original=self.p/'old';(original/'Stubs').mkdir(parents=True);(original/'Syscalls').mkdir()
  header=original/'Syscalls/value.h';header.write_text('#define VALUE 3\n')
  (self.src/'main.cpp').write_text('#include "../Syscalls/value.h"\n')
  self.data['roots'].update(stubs=str(original/'Stubs'),syscalls=str(original/'Syscalls'))
  self.data['units'][0]['include_paths']=[dict(kind='-iquote',path=str(original/'Stubs'))]
  self.run_stage()
  self.assertIn('../syscalls/value.h',(self.p/'staged/sources/core/main.cpp').read_text())

 def test_ambiguous_parent_header_resolution_rejected(self):
  # A shared legacy source cannot be rewritten to different targets silently.
  (self.src/'legacy.h').write_text('#include "../Syscalls/value.h"\n')
  for n in ['one','two']:
   root=self.p/n;(root/'Stubs').mkdir(parents=True);(root/'Syscalls').mkdir()
   (root/'Syscalls/value.h').write_text('#define VALUE 1\n')
   self.data['roots'][n]=str(root)
   self.data['units'].append(dict(id=n,source=str(self.src/'main.cpp'),include_paths=[dict(kind='-iquote',path=str(root/'Stubs'))]))
  with self.assertRaisesRegex(ValueError,'ambiguous'):self.run_stage()

 def test_unresolved_inactive_parent_include_is_deferred_not_invented(self):
  (self.src/'main.cpp').write_text('#ifdef _WIN32\n#include "../platform/windows.h"\n#endif\nint a;\n')
  r=self.run_stage()
  record=next(x for x in r['files'] if x['original'].endswith('main.cpp'))
  self.assertTrue(record['includes_rewritten'][0]['deferred'])
  self.assertFalse(r['dependency_closure_verified'])
  self.assertIn('../platform/windows.h',(self.p/'staged/sources/core/main.cpp').read_text())

 def test_compiler_revision_preserves_matching_ledger_and_invalidates_evidence(self):
  from source_graph import revise
  from clean_build import sha
  self.run_stage();root=self.p/'staged'
  r=revise(root/'recipe.json',root/'staging.json',{'main':{'flags':['-O3']}},root/'recipe-v2.json',root/'staging-v2.json')
  ledger=json.loads((root/'staging-v2.json').read_text())
  self.assertEqual(ledger['recipe_sha256'],sha(root/'recipe-v2.json'));self.assertFalse(ledger['dependency_closure_verified'])
  self.assertEqual(len(ledger['recipe_revisions']),1)
  with self.assertRaises(ValueError):revise(root/'recipe-v2.json',root/'staging.json',{'main':{'flags':['-O0']}},root/'recipe-v3.json',root/'staging-v3.json')
  with self.assertRaises(ValueError):revise(root/'recipe.json',root/'staging.json',{'main':{'source':'other.cpp'}},root/'recipe-v3.json',root/'staging-v3.json')
  with self.assertRaises(ValueError):revise(root/'recipe.json',root/'staging.json',{'main':{'flags':['-Xclang']}},root/'recipe-v3.json',root/'staging-v3.json')
  self.assertFalse((root/'recipe-v3.json').exists())


class AudioHeaderReconciliation(unittest.TestCase):
 def test_known_legacy_caller_is_reconciled_and_conflicting_family_rejected(self):
  from source_graph import owned_audio_header_edits
  from clean_build import validate_source_contracts
  canonical=(Path(__file__).resolve().parents[1]/'runtime/native_audio/ps2_audio.h').read_text()
  legacy=canonical.replace('#include <span>\n','').replace('    void setAudioReady(bool ready);\n    void submitPcm(std::span<const int16_t> samples);\n    void resetPcm();','    void setAudioReady(bool ready) { m_audioReady = ready; }')
  data={'backend.cpp':'void PS2AudioBackend::setAudioReady(bool ready) {} void PS2AudioBackend::submitPcm(int x) {}', 'caller/runtime/ps2_audio.h':legacy,'owned/ps2_audio.h':canonical}
  recipe=dict(files={k:'0'*64 for k in data},units=[dict(id='audio',source='backend.cpp')],targets=[dict(id='game',kind='executable',units=['audio'])])
  edits,contracts=owned_audio_header_edits(recipe,data.__getitem__)
  self.assertEqual([r['file'] for r in edits],['caller/runtime/ps2_audio.h'])
  recipe['source_contracts']=contracts
  with self.assertRaisesRegex(ValueError,'Conflicting header'):validate_source_contracts(recipe,data.__getitem__)
  for edit in edits:data[edit['file']]=edit['after']
  validate_source_contracts(recipe,data.__getitem__)
  data['caller/runtime/ps2_audio.h']+='\nvoid unreviewed_extension();'
  with self.assertRaisesRegex(ValueError,'Unrecognized'):owned_audio_header_edits(recipe,data.__getitem__)

 def test_legacy_only_backend_is_not_silently_upgraded(self):
  from source_graph import owned_audio_header_edits
  recipe=dict(files={'backend.cpp':'0'*64},units=[dict(id='old',source='backend.cpp')],targets=[])
  self.assertEqual(owned_audio_header_edits(recipe,lambda name:'// Legacy VAG-only backend'),([],[]))

 @unittest.skipUnless(__import__('shutil').which('clang++'),'Clang required')
 def test_split_translation_units_reproduce_silent_inline_bypass(self):
  from source_graph import owned_audio_header_edits
  canonical=(Path(__file__).resolve().parents[1]/'runtime/native_audio/ps2_audio.h').read_text()
  legacy=canonical.replace('#include <span>\n','').replace('    void setAudioReady(bool ready);\n    void submitPcm(std::span<const int16_t> samples);\n    void resetPcm();','    void setAudioReady(bool ready) { m_audioReady = ready; }')
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'owned').mkdir();(root/'caller').mkdir()
   (root/'owned/ps2_audio.h').write_text(canonical);(root/'caller/ps2_audio.h').write_text(legacy)
   backend='#include "owned/ps2_audio.h"\nint calls=0; struct PS2AudioBackend::Impl {}; PS2AudioBackend::PS2AudioBackend()=default; PS2AudioBackend::~PS2AudioBackend()=default; void PS2AudioBackend::setAudioReady(bool ready){++calls;} void PS2AudioBackend::submitPcm(std::span<const int16_t>){}\n'
   (root/'backend.cpp').write_text(backend)
   (root/'caller.cpp').write_text('#include "caller/ps2_audio.h"\nextern int calls; int main(){PS2AudioBackend b;b.setAudioReady(true);return calls==1?0:7;}\n')
   def build_and_run(name):
    result=subprocess.run(['clang++','-O3','-std=c++20',str(root/'backend.cpp'),str(root/'caller.cpp'),'-o',str(root/name)],capture_output=True,text=True)
    self.assertEqual(result.returncode,0,result.stderr)
    return subprocess.run([str(root/name)]).returncode
   self.assertEqual(build_and_run('legacy'),7)
   recipe=dict(files={'backend.cpp':'0'*64,'owned/ps2_audio.h':'0'*64,'caller/ps2_audio.h':'0'*64},units=[dict(id='audio',source='backend.cpp')],targets=[])
   edits,_=owned_audio_header_edits(recipe,lambda name:(root/name).read_text())
   for edit in edits:(root/edit['file']).write_text(edit['after'])
   self.assertEqual(build_and_run('corrected'),0)
