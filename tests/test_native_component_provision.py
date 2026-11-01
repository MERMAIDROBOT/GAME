from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
import native_components as component

class NativeComponentProvision(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  engine=self.root/'engine';engine.mkdir();(engine/'toolchain.lock.json').write_text('{}')
  source=engine/'runtime/gpu/source.cpp';source.parent.mkdir(parents=True);source.write_text('int source;')
  tools=self.root/'tools/bin';tools.mkdir(parents=True)
  for name in ['cmake','ninja','clang','clang++']:(tools/name).write_text('synthetic tool '+name)
  self.lab=SimpleNamespace(ROOT=engine,ENV=tools.parent,SOURCE_FILES=('runtime/gpu/source.cpp',))
  self.private=self.root/'private';self.private.mkdir();self.called=[]
  self.sources={name:dict(path=str(self.root/name),source_sha256='a'*64,revision='b'*40) for name in component.URLS}
  self.config=dict(sources={name:dict(path=row['path'],sha256=row['source_sha256']) for name,row in self.sources.items()})
  for context in [patch.object(component,'sources',return_value=self.sources),patch.object(component,'runtime_source',return_value=dict(revision='c'*40,source_sha256='d'*64)),patch.object(component.shutil,'which',side_effect=lambda name,**kwargs:str(tools/name))]:
   context.start();self.addCleanup(context.stop)
 def build(self,lab,output,**kwargs):
  self.called.append(output);output.mkdir();(output/'receipt.json').write_text(json.dumps(dict(status='native_graphics_source_build_passed')))
 def verify(self,lab,receipt):
  return dict(path=str(receipt.parent/'build/plugin.dylib'),component_receipt=dict(path=str(receipt),sha256=component.sha(receipt)))
 def test_first_use_builds_then_reuses_verified_receipt_and_source_change_rebuilds(self):
  with patch.object(component,'build',side_effect=self.build),patch.object(component,'verify',side_effect=self.verify):
   first=component.provision(self.lab,self.private,self.config)
   self.assertEqual(component.provision(self.lab,self.private,self.config),first);self.assertEqual(len(self.called),1)
   (self.lab.ROOT/'runtime/gpu/source.cpp').write_text('int changed;')
   changed=component.provision(self.lab,self.private,self.config)
  self.assertNotEqual(changed,first);self.assertEqual(len(self.called),2)
 def test_source_drift_or_unpinned_source_never_starts_build(self):
  with patch.object(component,'build') as build:
   self.config['sources']['volk']['sha256']='e'*64
   with self.assertRaisesRegex(ValueError,'pin changed'):component.provision(self.lab,self.private,self.config)
   self.config['sources']['volk'].pop('sha256')
   with self.assertRaisesRegex(ValueError,'source pin'):component.provision(self.lab,self.private,self.config)
   build.assert_not_called()
 def test_corrupt_completed_receipt_is_not_silently_replaced(self):
  with patch.object(component,'build',side_effect=self.build),patch.object(component,'verify',side_effect=self.verify):component.provision(self.lab,self.private,self.config)
  with patch.object(component,'build') as build,patch.object(component,'verify',side_effect=ValueError('artifact changed')):
   with self.assertRaisesRegex(ValueError,'artifact changed'):component.provision(self.lab,self.private,self.config)
   build.assert_not_called()
 def test_failed_attempt_is_preserved_and_only_one_retry_is_started(self):
  with patch.object(component,'build',side_effect=self.build),patch.object(component,'verify',side_effect=self.verify):component.provision(self.lab,self.private,self.config)
  old=self.called[0];(old/'receipt.json').write_text(json.dumps(dict(status='failed')))
  with patch.object(component,'build',side_effect=self.build),patch.object(component,'verify',side_effect=self.verify):component.provision(self.lab,self.private,self.config)
  self.assertTrue(old.is_dir());self.assertNotEqual(self.called[0],self.called[1]);self.assertEqual(len(self.called),2)
