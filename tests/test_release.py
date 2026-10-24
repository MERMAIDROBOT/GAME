"""Release packaging must preserve accepted engines and effective launch settings."""
import json
from pathlib import Path
import plistlib
import tempfile
from unittest import TestCase
from unittest.mock import patch
import release
import pipeline as lab

class Release(TestCase):
    def fixture(self,tmp):
        root=Path(tmp);app=root/'accepted.app';c=app/'Contents'
        (c/'Resources').mkdir(parents=True);(c/'Frameworks').mkdir();(c/'MacOS').mkdir()
        disc=root/'disc';disc.mkdir();exe=disc/'BOOT.ELF';exe.write_bytes(b'synthetic')
        env={'PS2_PORT_TEX_PACK':str(root),'PS2_PORT_ENHANCE_FXAA':'1','PS2_PORT_HW_GS_SCALE':'4','PS2_PORT_PAD_TRACE':'1'}
        (c/'Resources/RunConfiguration.plist').write_bytes(plistlib.dumps(dict(Executable=str(exe),DiscDirectory=str(disc),Environment=env)))
        (c/'Info.plist').write_bytes(plistlib.dumps(dict(LSEnvironment={'PS2_PORT_ENHANCE_FXAA':'0'})))
        for name in ('liblocal-parallel-gs-plugin.dylib','libMoltenVK.dylib','libclaude-hwgs.dylib'):
            (c/'Frameworks'/name).write_bytes(('synthetic '+name).encode())
        (c/'MacOS/ps2EntryRunner').write_bytes(b'synthetic runner')
        icon=root/'icon.icns';icon.write_bytes(b'synthetic icon')
        return root,app,icon

    def test_configuration_merge_and_hardware_renderer_pinning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            before=lab.digest(app/'Contents/Resources/RunConfiguration.plist')
            with patch.object(release.subprocess,'run'):
                report=release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            config=plistlib.loads((root/'release.app/Contents/Resources/RunConfiguration.plist').read_bytes())
            self.assertEqual(config['Environment']['PS2_PORT_ENHANCE_FXAA'],'0')
            self.assertEqual(config['Environment']['PS2_PORT_HW_GS_SCALE'],'4')
            self.assertNotIn('PS2_PORT_PAD_TRACE',config['Environment'])
            self.assertIn('Contents/Frameworks/libclaude-hwgs.dylib',report['accepted_engine_sha256'])
            self.assertEqual(before,lab.digest(app/'Contents/Resources/RunConfiguration.plist'))
            self.assertFalse(report['launch_smoke_verified'])

    def test_unknown_hw_diagnostic_cannot_sneak_through_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            p=app/'Contents/Resources/RunConfiguration.plist';d=plistlib.loads(p.read_bytes())
            d['Environment']['PS2_PORT_HW_GS_FORCE_TEST']='1';p.write_bytes(plistlib.dumps(d))
            with self.assertRaisesRegex(ValueError,'unknown release setting'):
                release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            self.assertFalse((root/'release.app').exists())
