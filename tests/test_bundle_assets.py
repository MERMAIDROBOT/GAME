"""Portable resources separate immutable assets from persistent saves."""
from pathlib import Path
import json,plistlib,tempfile,unittest
from bundle_assets import embed,resolve
from clean_build import sha
from asset_intake import inventory,pin
class BundleAssets(unittest.TestCase):
 def fixture(self,root):
  app=root/'Source.app';resources=app/'Contents/Resources';resources.mkdir(parents=True)
  (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.test.portable'}))
  disc=root/'disc';disc.mkdir();(disc/'BOOT.ELF').write_bytes(b'synthetic elf');(disc/'mc0').mkdir();(disc/'mc0/save').write_bytes(b'user save')
  manifest=root/'manifest.json';manifest.write_text(json.dumps({'files':[{'path':'BOOT.ELF','status':'copied','bytes':13,'sha256':sha(disc/'BOOT.ELF')}]}))
  config={'DiscDirectory':str(disc),'Executable':str(disc/'BOOT.ELF'),'DiscManifest':{'path':str(manifest),'sha256':sha(manifest)},'RuntimeCapabilities':{'separate_save_root':True},'Environment':{}}
  return app,config,manifest
 def test_move_preserves_assets_and_save_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app,config,_=self.fixture(root);result=embed(app,config)
   self.assertFalse((app/'Contents/Resources/RetailDisc/mc0').exists())
   self.assertEqual(result['BundledAssetPins']['DiscDirectory']['sha256'],pin(inventory(app/'Contents/Resources/RetailDisc')[1]))
   support=root/'support';before=resolve(app,result,support);moved=root/'Moved.app';app.rename(moved);after=resolve(moved,result,support)
   self.assertEqual(before['Environment']['PS2_PORT_SAVE_ROOT'],after['Environment']['PS2_PORT_SAVE_ROOT'])
   self.assertTrue(Path(after['Executable']).is_file());self.assertFalse(support.exists())
   self.assertEqual((root/'disc/mc0/save').read_bytes(),b'user save')
 def test_portable_storage_drops_legacy_external_memory_card_override(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app,config,_=self.fixture(root)
   original=root/'original-user-saves/mc0'
   config['Environment']['PS2_PORT_MC_ROOT']=str(original)
   config['Environment']['PS2_PORT_SAVE_ROOT']=str(root/'old-candidate/mc0')
   result=embed(app,config);resolved=resolve(app,result,root/'new-support')
   self.assertNotIn('PS2_PORT_MC_ROOT',result['Environment'])
   self.assertNotIn('PS2_PORT_SAVE_ROOT',result['Environment'])
   self.assertEqual(Path(resolved['Environment']['PS2_PORT_SAVE_ROOT']),root.resolve()/'new-support/local.test.portable/mc0')
   self.assertFalse(original.exists());self.assertFalse((root/'new-support').exists())
 def test_manifest_rejects_unsafe_records_before_copy(self):
  for change in ({'path':'../outside'},{'path':None},{'path':'/absolute'},{'bytes':True}):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);app,config,manifest=self.fixture(root);data=json.loads(manifest.read_text());data['files'][0].update(change);manifest.write_text(json.dumps(data));config['DiscManifest']['sha256']=sha(manifest)
    with self.assertRaises(ValueError):embed(app,config)
    self.assertFalse((app/'Contents/Resources/RetailDisc').exists())
 def test_changed_source_and_missing_capability_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app,config,_=self.fixture(root);config['RuntimeCapabilities']={}
   with self.assertRaises(ValueError):embed(app,config)
   config['RuntimeCapabilities']={'separate_save_root':True};(root/'disc/BOOT.ELF').write_bytes(b'changed bytes')
   with self.assertRaises(ValueError):embed(app,config)
 def test_resolver_rejects_bad_types_escape_and_symlink(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app,config,_=self.fixture(root)
   for data in ({'PortableStorage':1},{'BundledPaths':{'Executable':'../outside'}},{'BundledPaths':{'unknown':'x'}}):
    with self.assertRaises(ValueError):resolve(app,data)
   (app/'Contents/Resources/link').symlink_to(root/'disc')
   with self.assertRaises(ValueError):resolve(app,{'BundledPaths':{'DiscDirectory':'link'}})
 def test_pinned_textures_travel_with_bundle(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app,config,_=self.fixture(root);textures=root/'textures';textures.mkdir();(textures/'t.png').write_bytes(b'pixel')
   config['Environment']['PS2_PORT_TEX_PACK']=str(textures);config['AssetPins']={'PS2_PORT_TEX_PACK':{'sha256':pin(inventory(textures)[1])}}
   result=embed(app,config);resolved=resolve(app,result,root/'support')
   self.assertEqual((Path(resolved['Environment']['PS2_PORT_TEX_PACK'])/'t.png').read_bytes(),b'pixel')
   self.assertEqual(result['BundledAssetPins']['PS2_PORT_TEX_PACK']['sha256'],config['AssetPins']['PS2_PORT_TEX_PACK']['sha256'])
