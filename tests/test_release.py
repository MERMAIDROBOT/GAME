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
    def setUp(self):
        # These fixtures mock clang/codesign; no external game or GPU work runs.
        idle=patch.object(release,'assert_idle');idle.start();self.addCleanup(idle.stop)

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

    def test_appledouble_transport_metadata_preserved_outside_new_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,source,icon=self.fixture(tmp)
            candidate=root/'candidate.app'
            release.shutil.copytree(source,candidate)
            executable=candidate/'Contents/MacOS/ps2EntryRunner'
            before=lab.digest(executable)
            sidecar=executable.with_name('._'+executable.name)
            contents=b'\x00\x05\x16\x07\x00\x02\x00\x00'+b'synthetic AppleDouble'
            sidecar.write_bytes(contents)
            unrelated=sidecar.with_name('._ordinary-data');unrelated.write_bytes(b'not metadata')
            def clear_attributes(*args,**kwargs):
                self.assertFalse(sidecar.exists(),'Remove AppleDouble before recursive xattr')
            with patch.object(release.subprocess,'run',side_effect=clear_attributes) as run:
                result=release.prepare_signing_metadata(candidate)
            run.assert_called_once_with(['/usr/bin/xattr','-cr',str(candidate.resolve())],check=True)
            self.assertFalse(sidecar.exists())
            self.assertEqual((Path(result['backup'])/'Contents/MacOS'/sidecar.name).read_bytes(),contents)
            self.assertEqual(unrelated.read_bytes(),b'not metadata')
            self.assertEqual(before,lab.digest(executable))
            self.assertEqual(before,lab.digest(source/'Contents/MacOS/ps2EntryRunner'))
            self.assertEqual(len(result['appledouble_sidecars']),1)

    def test_existing_metadata_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,source,icon=self.fixture(tmp)
            source.with_suffix('.packaging-metadata').mkdir()
            with patch.object(release.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'Preserve existing'):
                    release.prepare_signing_metadata(source)
                run.assert_not_called()

    def test_signing_generated_sidecars_are_removed_between_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,source,icon=self.fixture(tmp);candidate=root/'release.app'
            sidecar=candidate/'Contents/.__CodeSignature'
            calls=[]
            def signing(args,**kwargs):
                if args[0]!='/usr/bin/codesign':return
                self.assertFalse(sidecar.exists(),'Signature metadata must be cleaned before next signing step')
                calls.append(args)
                if '--force' in args:
                    (candidate/'Contents/_CodeSignature').mkdir(exist_ok=True)
                    sidecar.write_bytes(b'\x00\x05\x16\x07\x00\x02\x00\x00'+b'generated metadata')
            with patch.object(release.subprocess,'run',side_effect=signing):
                result=release.package(source,candidate,'Test','1.0','test.game',icon,root/'run.log')
            self.assertEqual(len(calls),3)
            self.assertFalse(sidecar.exists())
            for phase in ('before-bundle','before-verify'):
                saved=Path(result['transport_metadata'][phase]['backup'])/'Contents/.__CodeSignature'
                self.assertTrue(saved.is_file())

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

    def test_legacy_external_memory_card_path_survives_repackaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,source,icon=self.fixture(tmp)
            path=source/'Contents/Resources/RunConfiguration.plist'
            config=plistlib.loads(path.read_bytes());cards=root/'original-user-saves/mc0'
            config['Environment']['PS2_PORT_MC_ROOT']=str(cards)
            path.write_bytes(plistlib.dumps(config));pin=lab.digest(path)
            with patch.object(release.subprocess,'run'):
                release.package(source,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            out=plistlib.loads((root/'release.app/Contents/Resources/RunConfiguration.plist').read_bytes())
            self.assertEqual(out['Environment']['PS2_PORT_MC_ROOT'],str(cards))
            self.assertNotIn('PS2_PORT_SAVE_ROOT',out['Environment'])
            self.assertFalse(cards.exists());self.assertEqual(lab.digest(path),pin)

    def test_invalid_legacy_memory_card_path_fails_before_copy(self):
        for value in ('relative/mc0','',42):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                root,source,icon=self.fixture(tmp)
                path=source/'Contents/Resources/RunConfiguration.plist'
                config=plistlib.loads(path.read_bytes());config['Environment']['PS2_PORT_MC_ROOT']=value
                path.write_bytes(plistlib.dumps(config))
                with self.assertRaisesRegex(ValueError,'absolute local path'):
                    release.package(source,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
                self.assertFalse((root/'release.app').exists())

    def test_declared_native_renderer_and_surface_settings_survive_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            old=app/'Contents/Frameworks/liblocal-parallel-gs-plugin.dylib'
            native=old.with_name('liblocal-native-gs-plugin.dylib');old.rename(native)
            p=app/'Contents/Resources/RunConfiguration.plist';d=plistlib.loads(p.read_bytes())
            image=root/'disc.iso';image.write_bytes(b'synthetic disc image')
            d['CdImage']=str(image)
            d['FrameworkEnvironment']={'PS2_PORT_PRIVATE_GS_PLUGIN':native.name,'PS2_PORT_PRIVATE_MOLTENVK':'libMoltenVK.dylib'}
            d['Environment'].update(PS2_PORT_HW_GS_ZERO_COPY='1',PS2_PORT_ENHANCE_HIRES_SCANOUT='1',PS2_PORT_HW_GS_ADAPTIVE='1',PS2_PORT_HW_GS_STALE_TARGET_FIX='1')
            p.write_bytes(plistlib.dumps(d))
            with patch.object(release.subprocess,'run'):
                report=release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            out=plistlib.loads((root/'release.app/Contents/Resources/RunConfiguration.plist').read_bytes())
            self.assertEqual(out['FrameworkEnvironment'],d['FrameworkEnvironment'])
            self.assertEqual(out['CdImage'],str(image))
            for key in ('PS2_PORT_HW_GS_ZERO_COPY','PS2_PORT_HW_GS_ADAPTIVE','PS2_PORT_HW_GS_STALE_TARGET_FIX'):
                self.assertEqual(out['Environment'][key],'1')
            self.assertIn('Contents/Frameworks/'+native.name,report['accepted_engine_sha256'])
            self.assertEqual(report['dependencies']['cd_image'],str(image))

    def test_bad_framework_binding_rejected_before_copy(self):
        for binding in ({'PS2_PORT_PRIVATE_GS_PLUGIN':'libmissing.dylib'},
                        {'PS2_PORT_PRIVATE_GS_PLUGIN':'../libescape.dylib'},
                        {'UNRELATED_SETTING':'libMoltenVK.dylib'}):
            with self.subTest(binding=binding),tempfile.TemporaryDirectory() as tmp:
                root,app,icon=self.fixture(tmp)
                p=app/'Contents/Resources/RunConfiguration.plist';d=plistlib.loads(p.read_bytes())
                d['FrameworkEnvironment']=binding;p.write_bytes(plistlib.dumps(d))
                with self.assertRaises(ValueError):
                    release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
                self.assertFalse((root/'release.app').exists())

    def test_foreign_compute_blocks_before_destination_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            with patch.object(release,'assert_idle',side_effect=RuntimeError('occupied')):
                with self.assertRaisesRegex(RuntimeError,'occupied'):
                    release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            self.assertFalse((root/'release.app').exists())

    def test_shared_engine_slot_blocks_release(self):
        from compute_slot import slot
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            with slot('synthetic-owner'):
                with self.assertRaisesRegex(RuntimeError,'compute slot'):
                    release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
            self.assertFalse((root/'release.app').exists())

    def test_configuration_drift_during_copy_is_not_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,app,icon=self.fixture(tmp)
            original=release.shutil.copytree
            def changing(*args,**kwargs):
                result=original(*args,**kwargs)
                p=app/'Contents/Info.plist';p.write_bytes(plistlib.dumps({'CFBundleName':'changed'}))
                return result
            with patch.object(release.shutil,'copytree',side_effect=changing),patch.object(release.subprocess,'run') as compiler:
                with self.assertRaisesRegex(RuntimeError,'configuration changed during packaging'):
                    release.package(app,root/'release.app','Test','1.0','test.game',icon,root/'run.log')
                compiler.assert_not_called()
            self.assertFalse((root/'release.release.json').exists())
