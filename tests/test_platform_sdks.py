from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
import platform_sdks,native_graph
from clean_build import sha


class PlatformSDKs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name).resolve()
  self.library=self.p/'libMoltenVK.dylib';self.library.write_bytes(b'synthetic SDK fixture, never executed')
  self.spec=dict(platform_sdks=[dict(kind='moltenvk',path=self.library.name,sha256=sha(self.library))])
 def test_explicit_sdk_is_pinned_and_never_claimed_source_built(self):
  with patch.object(platform_sdks,'inspect_library') as inspect:
   result=platform_sdks.frameworks(self.spec,self.p);self.assertEqual(native_graph.framework_inputs(dict(frameworks=result),self.p)['libMoltenVK.dylib'],self.library)
   self.assertEqual(inspect.call_count,2)
  self.assertFalse(result['libMoltenVK.dylib']['platform_sdk']['source_build_verified'])
 def test_changed_library_and_arbitrary_game_binaries_are_rejected(self):
  with patch.object(platform_sdks,'inspect_library') as inspect:
   self.library.write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'pin'):platform_sdks.frameworks(self.spec,self.p)
   inspect.assert_not_called()
  self.spec['platform_sdks'][0]['kind']='historical_game_runtime'
  with self.assertRaisesRegex(ValueError,'Unknown'):platform_sdks.frameworks(self.spec,self.p)
 def test_duplicate_escaping_symlink_and_mislabeled_inputs_are_rejected(self):
  with patch.object(platform_sdks,'inspect_library'):
   self.spec['platform_sdks']*=2
   with self.assertRaisesRegex(ValueError,'Duplicate'):platform_sdks.frameworks(self.spec,self.p)
   self.spec['platform_sdks']=self.spec['platform_sdks'][:1]
   for path in ['../libMoltenVK.dylib','runtime.a']:
    self.spec['platform_sdks'][0]['path']=path
    with self.assertRaises(ValueError):platform_sdks.frameworks(self.spec,self.p)
   self.spec['platform_sdks'][0]['path']='alias.dylib';(self.p/'alias.dylib').symlink_to(self.library)
   with self.assertRaises(ValueError):platform_sdks.frameworks(self.spec,self.p)
 def test_inspection_requires_arm64_dylib_and_does_not_load_library(self):
  with patch.object(platform_sdks.subprocess,'check_output',return_value='MH_MAGIC_64 X86_64 ALL DYLIB'):
   with self.assertRaisesRegex(ValueError,'ARM64'):platform_sdks.inspect_library(self.library)
  with patch.object(platform_sdks.subprocess,'check_output',return_value='MH_MAGIC_64 ARM64 ALL DYLIB'),patch('native_components.binary_dependencies',return_value=dict(inspected=True)):
   self.assertTrue(platform_sdks.inspect_library(self.library)['inspected'])
