from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
import native_components as components
class NativeComponents(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  root=self.p/'engine';(root/'runtime/gpu').mkdir(parents=True);(root/'runtime/gpu/a.cpp').write_text('int a;')
  work=self.p/'work';env=self.p/'tools';(env/'bin').mkdir(parents=True)
  for name in ['cmake','ninja']:(env/'bin'/name).write_text('fixture')
  runtime=work/'dependencies/PS2Recomp/ps2xRuntime/src/lib/gs';runtime.mkdir(parents=True);(runtime/'ps2_gs_memory.cpp').write_text('int memory;')
  lock={'ps2recomp':{'revision':'a'*40},'native_graphics_sources':{n:dict(url=u,revision='a'*40) for n,u in components.URLS.items()}}
  (root/'toolchain.lock.json').write_text(json.dumps(lock))
  self.lab=SimpleNamespace(ROOT=root,WORK=work,ENV=env,SOURCE=runtime.parents[3],LOCK=lock,SOURCE_FILES=['runtime/gpu/a.cpp'])
  self.overrides={}
  for name in components.URLS:
   path=self.p/name;path.mkdir();(path/'source.cpp').write_text('int dependency;');self.overrides[name]=str(path)
  self.query=patch('native_components.subprocess.check_output',side_effect=lambda args,**kw:'a'*40+'\n' if args[0]=='git' else '')
  self.query.start();self.addCleanup(self.query.stop)
 def test_plan_does_not_build_and_pins_all_public_sources(self):
  output=self.p/'out';r=components.build(self.lab,output,overrides=self.overrides)
  self.assertFalse(output.exists());self.assertEqual(r['status'],'native_graphics_planned');self.assertEqual(len(r['sources']),3);self.assertFalse(r['gameplay_verified'])
 def test_fresh_source_build_receipt_cannot_reuse_a_prior_output(self):
  output=self.p/'out'
  def command(args,*a,**kw):
   build=output/'build';build.mkdir(exist_ok=True)
   (build/'compile_commands.json').write_text('[]');(build/'liblocal-native-gs-plugin.dylib').write_bytes(b'compiled fixture')
  with patch('native.logged',side_effect=command),patch('native_components.binary_dependencies',return_value=dict(inspected=True,reference_backend_imports=False)):
   r=components.build(self.lab,output,overrides=self.overrides,execute=True)
  self.assertEqual(r['status'],'native_graphics_source_build_passed');self.assertTrue(r['source_candidate']);self.assertFalse(r['historical_objects_used'])
  with self.assertRaisesRegex(ValueError,'Preserve'):components.build(self.lab,output,overrides=self.overrides,execute=True)
 def test_changed_source_fails_before_it_can_be_reported_as_a_build(self):
  output=self.p/'out'
  def command(args,*a,**kw):
   build=output/'build';build.mkdir(exist_ok=True);(build/'compile_commands.json').write_text('[]');(build/'liblocal-native-gs-plugin.dylib').write_bytes(b'fixture')
   (self.lab.ROOT/'runtime/gpu/a.cpp').write_text('changed during build')
  with patch('native.logged',side_effect=command):
   with self.assertRaisesRegex(ValueError,'source changed'):components.build(self.lab,output,overrides=self.overrides,execute=True)
  self.assertEqual(json.loads((output/'receipt.json').read_text())['status'],'failed')
 def test_source_overlap_and_unknown_override_are_rejected(self):
  with self.assertRaisesRegex(ValueError,'overlaps'):components.build(self.lab,self.lab.SOURCE/'new-build',overrides=self.overrides)
  with self.assertRaisesRegex(ValueError,'Unknown'):components.build(self.lab,self.p/'out',overrides={'unknown':'/tmp'})

 def test_wrong_runtime_revision_blocks_the_build_before_execution(self):
  self.lab.LOCK['ps2recomp']['revision']='b'*40
  with self.assertRaisesRegex(ValueError,'runtime revision'):
   components.build(self.lab,self.p/'out',overrides=self.overrides)
  self.assertFalse((self.p/'out').exists())
 def test_shell_build_flags_and_runtime_experiments_do_not_leak_into_component(self):
  dirty={'CXXFLAGS':'old-library.a','CMAKE_TOOLCHAIN_FILE':'other.cmake','CPATH':'untracked','DYLD_INSERT_LIBRARIES':'injected.dylib','PS2_PORT_EXPERIMENT':'1','PS2HOST_TRACE':'1','PATH':'/usr/bin','TMPDIR':'/tmp'}
  with patch.dict('os.environ',dirty,clear=True):clean=components.build_environment()
  self.assertEqual(clean,{'PATH':'/usr/bin','TMPDIR':'/tmp'})
 def test_logged_build_uses_explicit_environment_without_reintroducing_shell_flags(self):
  import native
  from types import SimpleNamespace
  with patch.dict('os.environ',{'CXXFLAGS':'untracked.a'},clear=False),patch('native.subprocess.run',return_value=SimpleNamespace(returncode=0)) as command:
   native.logged(['fixture-cmake'],self.p/'build.log',base_environment={'PATH':'/usr/bin'})
  self.assertEqual(command.call_args.kwargs['env'],{'PATH':'/usr/bin'})
 def test_valid_appledouble_sidecar_is_not_a_second_compiled_plugin(self):
  output=self.p/'out'
  def command(args,*a,**kw):
   build=output/'build';build.mkdir(exist_ok=True)
   (build/'compile_commands.json').write_text('[]');(build/'liblocal-native-gs-plugin.dylib').write_bytes(b'compiled fixture')
   (build/'._liblocal-native-gs-plugin.dylib').write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00metadata')
  with patch('native.logged',side_effect=command),patch('native_components.binary_dependencies',return_value=dict(inspected=True,reference_backend_imports=False)):
   report=components.build(self.lab,output,overrides=self.overrides,execute=True)
  self.assertEqual(report['status'],'native_graphics_source_build_passed')
  self.assertTrue(report['plugin']['path'].endswith('/liblocal-native-gs-plugin.dylib'))

 def completed_component(self):
  from clean_build import sha
  output=self.p/'component';report=components.build(self.lab,output,overrides=self.overrides)
  build=output/'build';build.mkdir(parents=True);binary=build/'liblocal-native-gs-plugin.dylib';binary.write_bytes(b'compiled fixture')
  commands=build/'compile_commands.json';commands.write_text('[]')
  tool=self.lab.ENV/'bin/cmake'
  report.update(status='native_graphics_source_build_passed',plugin=dict(path=str(binary),sha256=sha(binary)),build_tools={str(tool):sha(tool)},compile_database_sha256=sha(commands))
  receipt=output/'receipt.json';receipt.write_text(json.dumps(report));return receipt,binary
 def test_component_import_rechecks_complete_source_and_artifact_provenance(self):
  receipt,binary=self.completed_component()
  with patch('native_components.binary_dependencies'):
   artifact=components.verify(self.lab,receipt)
   self.assertEqual(artifact['path'],str(binary));self.assertEqual(artifact['component_receipt']['path'],str(receipt.resolve()))
   binary.write_bytes(b'changed binary')
   with self.assertRaisesRegex(ValueError,'artifact changed'):components.verify(self.lab,receipt)
 def test_component_import_rejects_changed_source_and_unfinished_receipts(self):
  receipt,binary=self.completed_component();original=json.loads(receipt.read_text())
  unfinished=dict(original,status='running');receipt.write_text(json.dumps(unfinished))
  with self.assertRaisesRegex(ValueError,'successful source build'):components.verify(self.lab,receipt)
  receipt.write_text(json.dumps(original));(self.lab.ROOT/'runtime/gpu/a.cpp').write_text('changed source')
  with self.assertRaisesRegex(ValueError,'source inventory changed'):components.verify(self.lab,receipt)
