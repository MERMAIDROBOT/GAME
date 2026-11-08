from pathlib import Path
import json,tempfile,unittest
from launch_config import prepare,directory_pin

class LaunchConfig(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name).resolve()
 def test_explicit_4k_settings_and_bundle_relative_framework_binding(self):
  p={'native_launch':{'environment':{'PS2_PORT_ENHANCE_FXAA':'1','PS2_PORT_HW_GS_SCALE':'4','PS2_PORT_ENHANCE_WINSIZE':'1600x900'},'framework_bindings':{'PS2_PORT_PRIVATE_GS_PLUGIN':'libgpu.dylib'},'diagnostics':False,'renderer_packet_batch':True}}
  r=prepare(p,self.p,{'libgpu.dylib':self.p/'source-build/libgpu.dylib'})
  self.assertEqual(r['Environment'],dict(p['native_launch']['environment'],PS2_PORT_HW_GS_PIPELINE_CACHE_DIR=str(self.p/'cache/gpu-pipelines')));self.assertEqual(r['FrameworkEnvironment'],p['native_launch']['framework_bindings']);self.assertFalse(r['LocalDiagnostics'])
 def test_adaptive_settings_are_explicit_and_bounded(self):
  environment={'PS2_PORT_HW_GS_ADAPTIVE':'1','PS2_PORT_HW_GS_STALE_TARGET_FIX':'1','PS2_PORT_HW_GS_ADAPTIVE_STALLS':'6'}
  self.assertEqual(prepare({'native_launch':{'environment':environment}},self.p,{})['Environment']['PS2_PORT_HW_GS_ADAPTIVE_STALLS'],'6')
  for value in ['0','-1','1.5','100001','infinity']:
   with self.subTest(value=value),self.assertRaises(ValueError):prepare({'native_launch':{'environment':{'PS2_PORT_HW_GS_ADAPTIVE_STALLS':value}}},self.p,{})
 def test_diagnostic_automated_input_and_unknown_settings_rejected(self):
  for key in ['PS2_PORT_PAD_COMMAND','PS2_PORT_HEADLESS','DYLD_INSERT_LIBRARIES','PS2_PORT_IO_ROOT','PS2_PORT_ENHANCE_UNKNOWN']:
   with self.subTest(key=key),self.assertRaises(ValueError):prepare({'native_launch':{'environment':{key:'1'}}},self.p,{})
  with self.assertRaises(ValueError):prepare({'native_launch':{'environment':{'PS2_PORT_ENHANCE_FXAA':True}}},self.p,{})
 def test_local_texture_pack_is_pinned_and_tampering_rejected(self):
  pack=self.p/'textures';pack.mkdir();(pack/'image.png').write_bytes(b'image fixture')
  pin=directory_pin(pack);p={'native_launch':{'paths':{'PS2_PORT_TEX_PACK':{'path':'textures','sha256':pin['sha256']}}}}
  r=prepare(p,self.p,{});self.assertEqual(r['assets']['PS2_PORT_TEX_PACK']['files'],1)
  (pack/'image.png').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'profile pin'):prepare(p,self.p,{})
 def test_verified_metadata_does_not_change_asset_identity(self):
  pack=self.p/'textures';pack.mkdir();(pack/'image.png').write_bytes(b'image fixture')
  original=directory_pin(pack)
  sidecar=pack/'._image.png';sidecar.write_bytes(bytes.fromhex('0005160700020000')+b'metadata')
  self.assertEqual(directory_pin(pack),original)
  sidecar.write_bytes(b'unknown source bytes')
  self.assertNotEqual(directory_pin(pack)['sha256'],original['sha256'])
 def test_paths_frameworks_and_symlinks_cannot_escape(self):
  for spec in [{'paths':{'PS2_PORT_TEX_PACK':{'path':'../elsewhere'}}},{'framework_bindings':{'PS2_PORT_PRIVATE_GS_PLUGIN':'missing.dylib'}},{'paths':{'PS2_PORT_TEX_PACK':{'path':'/outside'}}}]:
   with self.subTest(spec=spec),self.assertRaises(ValueError):prepare({'native_launch':spec},self.p,{})
  pack=self.p/'textures';pack.mkdir();(pack/'link.png').symlink_to('/etc/hosts')
  with self.assertRaisesRegex(ValueError,'symlink'):directory_pin(pack)
 def test_mutable_cache_is_explicit_but_not_a_source_asset(self):
  p={'native_launch':{'paths':{'PS2_PORT_HW_GS_PIPELINE_CACHE_DIR':{'path':'cache/shaders'}}}}
  r=prepare(p,self.p,{});self.assertFalse((self.p/'cache').exists());self.assertEqual(r['assets'],{})
  self.assertEqual(r['Environment']['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR'],str(self.p/'cache/shaders'))
 def test_movie_trace_is_explicit_opt_in_and_cannot_claim_to_be_disabled(self):
  r=prepare({'native_launch':{'environment':{'PS2_PORT_MPEG_TRACE':'1'}}},self.p,{})
  self.assertEqual(r['Environment']['PS2_PORT_MPEG_TRACE'],'1')
  with self.assertRaises(ValueError):prepare({'native_launch':{'environment':{'PS2_PORT_MPEG_TRACE':'0'}}},self.p,{})

 def test_overlay_miss_trace_is_opt_in_and_presence_based(self):
  r=prepare({'native_launch':{'environment':{'PS2_PORT_OVERLAY_MISS_TRACE':'1'}}},self.p,{})
  self.assertEqual(r['Environment']['PS2_PORT_OVERLAY_MISS_TRACE'],'1')
  self.assertNotIn('PS2_PORT_OVERLAY_MISS_TRACE',prepare({},self.p,{})['Environment'])
  with self.assertRaises(ValueError):prepare({'native_launch':{'environment':{'PS2_PORT_OVERLAY_MISS_TRACE':'0'}}},self.p,{})

 def test_gs_recording_is_local_bounded_and_has_no_packaging_side_effect(self):
  spec={'environment':{'PS2_PORT_HW_GS_RECORD_MB':'256'},'paths':{'PS2_PORT_HW_GS_RECORD':{'path':'diagnostics/transition.pgscap'}}}
  r=prepare({'native_launch':spec},self.p,{})
  self.assertEqual(r['Environment']['PS2_PORT_HW_GS_RECORD'],str(self.p/'diagnostics/transition.pgscap'))
  self.assertEqual(r['assets'],{});self.assertFalse((self.p/'diagnostics').exists())
  for limit in ['0','513','-1','256.5']:
   spec['environment']['PS2_PORT_HW_GS_RECORD_MB']=limit
   with self.subTest(limit=limit),self.assertRaises(ValueError):prepare({'native_launch':spec},self.p,{})
  with self.assertRaises(ValueError):prepare({'native_launch':{'paths':spec['paths']}},self.p,{})
  with self.assertRaises(ValueError):prepare({'native_launch':{'environment':{'PS2_PORT_HW_GS_RECORD_MB':'256'}}},self.p,{})

 def test_window_movie_and_control_settings_are_bounded(self):
  settings={'PS2_PORT_ENHANCE_WINSIZE':'1600x900','PS2_PORT_ENHANCE_MOVIE_FILL':'off','PS2_PORT_SHOW_CONTROLS':'0','PS2_PORT_ENHANCE_HIDPI':'1'}
  self.assertEqual(prepare({'native_launch':{'environment':settings}},self.p,{})['Environment'],settings)
  for key,value in [('PS2_PORT_ENHANCE_WINSIZE','100x100'),('PS2_PORT_ENHANCE_WINSIZE','1600x900;bad'),('PS2_PORT_ENHANCE_WINSIZE','9999x9999'),('PS2_PORT_ENHANCE_MOVIE_FILL','auto')]:
   with self.subTest(value=value),self.assertRaises(ValueError):prepare({'native_launch':{'environment':{key:value}}},self.p,{})

 def test_defaults_do_not_enable_title_specific_settings(self):
  r=prepare({},self.p,{});self.assertEqual(r['Environment'],{});self.assertEqual(r['FrameworkEnvironment'],{})

 def test_source_graphics_binding_gets_a_cache_without_creating_files_during_packaging(self):
  p={'native_launch':{'framework_bindings':{'PS2_PORT_PRIVATE_GS_PLUGIN':'libnative.dylib'}}}
  r=prepare(p,self.p,{'libnative.dylib':self.p/'libnative.dylib'})
  self.assertEqual(r['Environment']['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR'],str(self.p/'cache/gpu-pipelines'));self.assertFalse((self.p/'cache').exists());self.assertEqual(r['assets'],{})

 def test_surface_transport_is_explicit_and_requires_matching_transport_inputs(self):
  env={'PS2_PORT_HW_GS_ZERO_COPY':'1','PS2_PORT_ENHANCE_HIRES_SCANOUT':'1'}
  with self.assertRaisesRegex(ValueError,'Surface transport requires'):
   prepare({'native_launch':{'environment':env}},self.p,{})
  bindings={'PS2_PORT_PRIVATE_GS_PLUGIN':'native.dylib'}
  with self.assertRaisesRegex(ValueError,'Surface transport requires'):
   prepare({'native_launch':{'environment':{'PS2_PORT_HW_GS_ZERO_COPY':'1'},'framework_bindings':bindings}},self.p,{'native.dylib':self.p/'native.dylib'})
  result=prepare({'native_launch':{'environment':env,'framework_bindings':bindings}},self.p,{'native.dylib':self.p/'native.dylib'})
  self.assertEqual(result['Environment']['PS2_PORT_HW_GS_ZERO_COPY'],'1')
  disabled=prepare({'native_launch':{'environment':{'PS2_PORT_HW_GS_ZERO_COPY':'0'}}},self.p,{})
  self.assertEqual(disabled['Environment']['PS2_PORT_HW_GS_ZERO_COPY'],'0')

 def test_execution_controls_reject_non_boolean_profile_values(self):
  from launch_config import execution_environment
  launch=prepare({},self.p,{})
  for field in ('renderer_worker','async_presentation','graphics_history'):
   with self.subTest(field=field),self.assertRaisesRegex(ValueError,'boolean'):
    execution_environment({field:'1'},launch,{},self.p)

 def test_revision_specific_special_timer_opt_out_is_explicit(self):
  r=prepare({'native_launch':{'environment':{'PS2_PORT_SPECIAL_TIMER_RESET':'0'}}},self.p,{})
  self.assertEqual(r['Environment']['PS2_PORT_SPECIAL_TIMER_RESET'],'0')
  self.assertNotIn('PS2_PORT_SPECIAL_TIMER_RESET',prepare({},self.p,{})['Environment'])
 def test_revision_specific_per_special_timer_opt_out_is_explicit(self):
  r=prepare({'native_launch':{'environment':{'PS2_PORT_SPECIAL_TIMER_PER_SPECIAL':'0'}}},self.p,{})
  self.assertEqual(r['Environment']['PS2_PORT_SPECIAL_TIMER_PER_SPECIAL'],'0')
  self.assertNotIn('PS2_PORT_SPECIAL_TIMER_PER_SPECIAL',prepare({},self.p,{})['Environment'])
