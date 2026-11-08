#!/usr/bin/env python3
"""Local, resumable disc-to-C++ orchestration. No upload or publish commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent
DEFAULT_WORK = ROOT.parent.parent/'work' if ROOT.parent.name=='outputs' else ROOT/'.work'
WORK = Path(os.environ.get('PS2_PORT_LAB_WORK', str(DEFAULT_WORK))).resolve()
SOURCE = WORK / 'dependencies/PS2Recomp'
BUILD = WORK / 'build/ps2recomp'
ENV = Path(os.environ.get('PS2_PORT_LAB_TOOLCHAIN',str(WORK/'toolchain'))).resolve()
LOCK = json.loads((ROOT / 'toolchain.lock.json').read_text())
NETWORK_APIS = re.compile(r'\b(?:socket|connect|sendto|sendmsg|curl_\w*|WinHttp\w*|InternetOpen\w*|popen|system)\s*\(|#include[^\n]*(?:curl|socket|http|asio|winsock)', re.I)
SOURCE_FILES = ('patches/native-callback-context-lifetime.patch',) + ('patches/host-clock-accounting.patch',) + ('audio_evidence.py','tests/test_audio_evidence.py',) + ('runtime/host_input/direction_recovery.hpp','patches/touch-direction-recovery.patch','tests/direction_recovery.cpp','tests/test_direction_recovery.py',) + ('replay_receipt_versions.json','tests/test_runtime_capabilities.py','bundle_assets.py','tests/test_bundle_assets.py','cmake/BundleConfiguration.h','runtime/host_environment/storage_paths.hpp','patches/native-save-root.patch','tests/test_storage_paths.py','preserved_disc.py','tests/test_preserved_disc.py','offline_toolchain.py','tests/test_offline_toolchain.py','platform_sdks.py','tests/test_platform_sdks.py','source_corrections.py','tests/test_source_corrections.py','runtime/gpu/host-adapter/alpha_cut_rule.hpp','tests/alpha_cut_rule.cpp','tests/test_alpha_cut_rule.py','tests/mac_vulkan_surface.cpp','tests/test_mac_vulkan_surface.py','tests/mac_surface_presenter.cpp','tests/mac_surface_producer.cpp','tests/test_mac_surface_presenter.py','tests/surface_owner.cpp','tests/test_surface_owner.py','tests/test_surface_transport.py','surface_transport.py','runtime/presentation/mac_surface_presenter.hpp','runtime/presentation/surface_token.hpp','runtime/presentation/surface_consumer.hpp','tests/surface_consumer.cpp','tests/test_surface_consumer.py','runtime/gpu/src/surface_leases.h','runtime/gpu/include/ps2gpu/surface_api.h','tests/gpu_surface_leases.cpp','tests/test_gpu_surface_leases.py','runtime/host_input/ORIGIN.json','patches/host-input-events.patch','patches/mpeg-stream-callbacks.patch','runtime/host_environment/ORIGIN.json','runtime/host_environment/env_cache.c','tests/host_environment.c','tests/test_host_environment.py','tests/test_attach_game_c_sources.py','runtime/gpu/host-model/include/ps2rt/types.h','runtime/gpu/host-model/include/ps2rt/generated.h','runtime/gpu/host-model/include/ps2rt/lifted.h','graph_amend.py','tests/test_graph_amend.py','runtime/gpu/native-plugin/gs_register_addr.hpp','patches/gif-image2.patch','tests/test_image2_patch.py','native_components.py','tests/test_native_components.py','tests/native_plugin.cpp','tests/test_native_plugin.py','runtime/gpu/native-plugin/CMakeLists.txt','runtime/gpu/native-plugin/COPYING.LGPLv3','runtime/gpu/native-plugin/ORIGIN.json','runtime/gpu/native-plugin/field_publisher.hpp','runtime/gpu/native-plugin/field_readiness.hpp','runtime/gpu/native-plugin/gs_registers.hpp','runtime/gpu/native-plugin/hw_owner.hpp','runtime/gpu/native-plugin/image_stamp.hpp','runtime/gpu/native-plugin/native_registers.hpp','runtime/gpu/native-plugin/plugin.cpp','runtime/gpu/native-plugin/queued_owner.hpp','runtime/gpu/native-plugin/raw_backend.hpp','runtime/gpu/native-plugin/raw_field.hpp','runtime/gpu/native-plugin/scanout_latch.hpp','runtime/gpu/native-plugin/serial_queue.hpp','runtime/gpu/native-plugin/staging.hpp','runtime/gpu/host-adapter/cache_hash.hpp','runtime/gpu/host-adapter/hwgs_capi.cpp','runtime/gpu/host-adapter/host_services.cpp','runtime/gpu/host-adapter/host_defaults.cpp','runtime/gpu/host-model/src/gs_thread.cpp','runtime/gpu/host-model/src/profile.cpp','runtime/gpu/third_party/stb/stb_image.h','runtime/gpu/third_party/stb/stb_image_write.h','runtime/gpu/third_party/stb/LICENSE','link_recipe.py','tests/test_link_recipe.py','sdk_policy.py','tests/test_sdk_audit.py','tool_state.py','tests/test_tool_state.py','compute_slot.py','tests/test_compute_slot.py','abi_layouts.py','tests/test_abi_layouts.py','object_symbols.py','tests/test_object_symbols.py','runtime/abi/sdk_policy.h','patches/sdk-audio-policy.patch','tests/test_sdk_policy.py','make_plan.py','tests/test_make_plan.py','archive_selection.py','tests/test_archive_selection.py','object_controls.py','tests/test_object_controls.py','patches/ee64-varargs.patch','runtime/abi/Support.h','runtime/abi/ORIGIN.json','tests/ee64_varargs.cpp','tests/test_ee64_varargs.py','merge_graphs.py','tests/test_merge_graphs.py','launch_config.py','tests/test_launch_config.py','compile_recipe.py','tests/test_compile_recipe.py','native_graph.py','tests/test_native_graph.py','archive_sources.py','tests/test_archive_sources.py','source_graph.py','tests/test_source_graph.py','tests/test_field_trace.py','clean_build.py','build_closure.py','CLEAN-BUILD.md','tests/test_clean_build.py','tests/test_build_closure.py','tests/gs_touch.cpp','tests/test_gs_touch.py','runtime/gpu/host-adapter/vram_ranges.hpp','runtime/gpu/host-model/CMakeLists.txt','runtime/gpu/host-model/include/ps2host/disc.h','runtime/gpu/host-model/include/ps2host/dma.h','runtime/gpu/host-model/include/ps2host/gs.h','runtime/gpu/host-model/include/ps2host/host.h','runtime/gpu/host-model/include/ps2host/iop_vm.h','runtime/gpu/host-model/include/ps2host/ipu.h','runtime/gpu/host-model/include/ps2host/mc2.h','runtime/gpu/host-model/include/ps2host/memory.h','runtime/gpu/host-model/include/ps2host/path3.h','runtime/gpu/host-model/include/ps2host/profile.h','runtime/gpu/host-model/include/ps2host/run.h','runtime/gpu/host-model/include/ps2host/settings.h','runtime/gpu/host-model/include/ps2host/texture_pack.h','runtime/gpu/host-model/include/ps2host/timeline.h','runtime/gpu/host-model/include/ps2host/vif.h','runtime/gpu/host-model/include/ps2host/vu.h','runtime/gpu/host-model/include/ps2host/vu_core.h','runtime/gpu/host-model/src/gs.cpp','runtime/gpu/host-model/src/gs_draw.cpp','runtime/gpu/host-model/src/gs_memory.cpp','runtime/gpu/host-adapter/texpack/hw_runtime_pack.hpp','runtime/gpu/host-adapter/texpack/claude_tex_pack_core.hpp','runtime/gpu/host-adapter/texpack/blob_prefetch.hpp','runtime/presentation/completed_field.hpp','tests/frame_ownership.cpp','tests/test_frame_ownership.py','tests/test_release.py','tests/test_gpu_components.py','runtime/gpu/CMakeLists.txt','runtime/gpu/LICENSE','runtime/gpu/ORIGIN.json','runtime/gpu/host-adapter/gpu_bridge.cpp','runtime/gpu/host-adapter/runtime_mips.hpp','runtime/gpu/include/ps2gpu/display.h','runtime/gpu/include/ps2gpu/gs_renderer.h','runtime/gpu/shaders/date.frag','runtime/gpu/shaders/date.vert','runtime/gpu/shaders/decode.comp','runtime/gpu/shaders/depth_load.frag','runtime/gpu/shaders/downsample.comp','runtime/gpu/shaders/fxaa.comp','runtime/gpu/shaders/gs.frag','runtime/gpu/shaders/gs.vert','runtime/gpu/shaders/gs_address.glsl','runtime/gpu/shaders/patch.comp','runtime/gpu/shaders/smaa_blend.comp','runtime/gpu/shaders/smaa_common.glsl','runtime/gpu/shaders/smaa_edges.comp','runtime/gpu/shaders/smaa_weights.comp','runtime/gpu/shaders/swizzle.comp','runtime/gpu/src/gpu.cpp','runtime/gpu/src/gpu_internal.h','runtime/gpu/src/gs_renderer.cpp','runtime/gpu/src/threaded_renderer.cpp','runtime/gpu/src/vulkan_display.cpp','runtime/gpu/tests/display_smoke.cpp','runtime/gpu/tests/renderer_tests.cpp','runtime/gpu/third_party/smaa/AreaTex.h','runtime/gpu/third_party/smaa/LICENSE.txt','runtime/gpu/third_party/smaa/README.md','runtime/gpu/third_party/smaa/SMAA.glsl','runtime/gpu/third_party/smaa/SearchTex.h','tests/gpu_residency.cpp','patches/host-input.patch','runtime/host_input/ps2_pad.cpp','patches/native-pcm.patch','runtime/native_audio/ps2_audio.cpp','runtime/native_audio/ps2_audio.h','runtime/native_audio/ORIGIN.json','tests/native_audio_drift.cpp','tests/reference/raylib/raylib.h','tests/test_native_audio.py','gif_packets.py', 'tests/test_gif_packets.py', 'branches.py', 'port_checks.py', 'upstreams.py', 'tests/test_branches.py', 'tests/test_port_checks.py', 'tests/test_upstreams.py', 'PORT-QUALIFICATION.md', 'UPSTREAM-REVIEW-2026-10-24.md', 'pipeline.py', 'native.py', 'scenarios.py', 'README.md', 'CONTROLS.md', 'PRIVACY.md',
                'STATUS.md', 'RUNTIME_COMPARISON.md', '.gitignore', 'toolchain.lock.json',
                'profiles/example-game.json', 'cmake/AttachGame.cmake', 'cmake/LocalLauncher.m',
                'patches/runtime-diagnostics.patch', 'patches/ps2xIOP-port-lab.patch', 'tests/test_iop_patch.py', 'tests/test_pipeline.py',
                'tests/test_native.py', 'tests/test_scenarios.py', 'tests/runtime_timer.cpp',
                'vectors.py', 'tools/vu1_recomp.cpp', 'tools/PS2Recomp-LICENSE', 'tests/test_vectors.py',
                'tests/host_pad.cpp', 'tests/host_presentation.cpp', 'rawgs.py', 'tests/test_rawgs.py',
                'replay.py', 'tests/test_replay.py', 'memory.py', 'tests/test_memory.py',
                'compiler.py', 'tests/test_compiler.py', 'COMPILER.md',
                'dependencies.py', 'tests/test_dependencies.py',
                'toolchain.py', 'tests/test_toolchain.py',
                'adapters.py', 'tests/test_adapters.py',
                'textures.py', 'tests/test_textures.py', 'TEXTURES.md',
                'tim2.py', 'tests/test_tim2.py', 'local_sr.py', 'tests/test_local_sr.py',
                'sr_batch.py', 'tests/test_sr_batch.py', 'tools/local_text_ocr.m',
                'ate.py', 'tests/test_ate.py', 'next_game.py', 'tests/test_next_game.py',
                'release.py', 'cmake/PlayableLauncher.m', 'tools/make_app_icon.m',
                'NEXT-GAME.md', 'LESSONS.md', 'knowledge.py', 'knowledge.json',
                'tests/test_knowledge.py', 'ENGINE.md', 'enhancements.py', 'enhancements.json',
                'tests/test_enhancements.py', 'target_cli.py', 'tests/test_targets.py',
                'CONTRIBUTING.md', 'LICENSE', 'UPSTREAM-NOTICES.md',
                '.github/workflows/both-targets.yml', '.github/PULL_REQUEST_TEMPLATE.md',
                'source_freeze.py', 'tests/test_source_freeze.py', 'requirements.txt', 'platform_adapter.py', 'tests/test_platform_adapter.py', 'PLATFORMS.md', 'run.py', 'tests/test_entrypoint.py', 'tests/test_engine_fixes.py',
                'mmi.py', 'tests/test_mmi.py', 'vu0_macro.py', 'tests/test_vu0_macro.py', 'texture_seams.py', 'tests/test_texture_seams.py',
                'feedback.py', 'tests/test_feedback.py', 'RUNTIME-FEEDBACK.md',
                'recovery.py', 'tests/test_recovery.py', 'tests/fixtures/integer-oracle.json', 'tools/recovery_runner.hpp', 'FUNCTION-RECOVERY.md', 'recovery_core/__init__.py', 'recovery_core/emu.py', 'recovery_core/vu0.py', 'recovery_core/layout.py', 'recovery_core/r5900.py', 'recovery_core/symexec.py', 'recovery_core/LICENSE', 'recovery_core/ORIGIN.json',
                'vu_dependencies.py', 'vu_dependency_core.py', 'tests/test_vu_dependencies.py', 'VU-DEPENDENCIES.md',
                'conformance.py', 'fpu.py', 'tests/test_fpu.py', 'tests/test_conformance.py',
                'tests/fixtures/fpu-hardware.json', 'tools/ps2_scalar_fpu.h',
                'tools/reference/ps2float.c', 'tools/reference/ps2float.h',
                'tools/reference/COPYING.LESSER', 'tools/reference/COPYING.GPLv3',
                'patches/scalar-fpu.patch', 'FPU.md',
                'patches/runtime-title-conformance.patch', 'tests/test_title_conformance.py', 'tests/test_runtime_patch_stack.py',
                'cdimage.py', 'pointer_entries.py', 'code_census.py', 'overlays.py', 'tests/test_overlays.py', 'runtime/overlays/overlay_dispatch.hpp', 'patches/overlay-dispatch.patch', 'tests/overlay_dispatch.cpp', 'tests/test_overlay_dispatch.py', 'tools/recomp/stub_interior_entries.h','tools/recomp/mmi_corrections.h','runtime/mmi/ps2_mmi_ops.h','patches/mmi-reference-forms.patch','tests/test_mmi_reference_forms.py','tools/recomp/vu0_corrections.h','runtime/mmi/ps2_vu0_ops.h','patches/vu0-reference-forms.patch','tests/test_vu0_reference_forms.py','patches/link-branch-reference.patch','tests/test_link_branch_reference.py','patches/fpu-reference-flags.patch','tests/test_fpu_reference_flags.py','runtime/mmi/ps2_vu_float.h','patches/vu-micro-reference.patch','vu_micro_check.py','vu_hardware_check.py','tests/fixtures/vu-hardware.json','tests/test_vu_micro_reference.py', 'patches/gs-host-rounding.patch', 'tests/gs_host_rounding.cpp', 'tests/test_gs_host_rounding.py', 'patches/vu-run-to-end.patch', 'patches/vu-vf0-constant.patch', 'tests/vu_run_to_end.cpp', 'tests/vu_vf0_constant.cpp', 'tests/fixtures/vu-lockstep-regressions.json', 'vu_lockstep_check.py', 'tests/test_vu_lockstep.py', 'patches/iop-shared-pool.patch', 'tests/test_iop_shared_pool.py', 'behaviour_check.py', 'tests/test_behaviour_check.py', 'patches/stub-interior-entries.patch', 'tests/stub_interior_entries.cpp', 'tests/test_stub_interior_entries.py', 'tests/test_cdimage.py', 'tests/test_pointer_entries.py', 'tests/test_code_census.py',
                'cadence.py', 'tests/test_cadence.py', 'NEW-REPOSITORIES-2026-10-17.md', 'CADENCE.md',
                'platforms/macos/adapter.json', 'platforms/macos/macos_adapter.py',
                'ab_check.py', 'tests/test_ab_check.py',
                'patches/scalar-fpu-compare.patch', 'patches/runtime-hotpaths.patch',
                'tests/runtime_lazy_timer.cpp', 'tests/runtime_vif_unpack.cpp',
                'tests/test_runtime_hotpaths.py', 'runtime_hotpaths.py', 'RUNTIME-HOTPATHS.md',
                'tests/test_fpu_compare_emission.py', 'tests/fpu_translator_probe.cpp', 'tests/fpu-translator-support/ps2recomp/code_generator.h', 'tests/reference/fpu-translator/ORIGIN.json', 'tests/reference/fpu-translator/fmt/LICENSE', 'tests/reference/fpu-translator/fmt/include/fmt/base.h', 'tests/reference/fpu-translator/fmt/include/fmt/format-inl.h', 'tests/reference/fpu-translator/fmt/include/fmt/format.h', 'tests/reference/fpu-translator/ps2xRecomp/include/ps2recomp/Translators/fpu_translator.h', 'tests/reference/fpu-translator/ps2xRecomp/include/ps2recomp/codegen_helpers.h', 'tests/reference/fpu-translator/ps2xRecomp/include/ps2recomp/instructions.h', 'tests/reference/fpu-translator/ps2xRecomp/include/ps2recomp/types.h', 'tests/reference/fpu-translator/ps2xRecomp/src/lib/fpu_translator.cpp')


SOURCE_FILES = ('cmake/SourceInventory.cmake', 'runtime/gpu/host-adapter/target_freshness.hpp', 'runtime/gpu/host-adapter/adaptive_scale_rule.hpp', 'tests/renderer_carryforward_rules.cpp', 'tests/hw_owner_live_scale.cpp', 'tests/test_renderer_carryforward.py') + SOURCE_FILES


SOURCE_FILES = ('tests/test_native_component_provision.py',) + SOURCE_FILES
# Claude 2026-11-04: the GPU renderer builds from this repository alone (vendored Vulkan headers, generated
# loader, committed SPIR-V), plus the shared display, renderer-host and frame-stride runtime patches.
SOURCE_FILES = SOURCE_FILES + tuple(sorted(
    str(p.relative_to(ROOT)) for folder in ('runtime/gpu/third_party/vulkan-headers', 'runtime/gpu/third_party/vk_loader',
                                            'runtime/gpu/shaders/spirv')
    for p in (ROOT / folder).rglob('*') if p.is_file() and not p.name.startswith('.'))) + (
    'tools/gen_vk_loader.py', 'tools/gen_shaders.py', 'patches/presentation-fidelity.patch',
    'patches/gpu-renderer-host.patch', 'patches/window-thread-failure.patch', 'patches/hd-frame-stride.patch',
    'patches/vsync-setting.patch', 'tests/test_gpu_offline.py', 'tests/test_display_path.py', 'tests/display_path.cpp', 'tests/test_app_storage.py', 'patches/guest-heap-exact.patch', 'tests/guest_heap_layout.cpp', 'tests/test_guest_heap_layout.py', 'tests/scanout_transition.cpp', 'tests/test_scanout_transition.py', 'isa_r5900.py', 'binary_rebuild.py', 'tests/test_isa_r5900.py', 'tests/test_binary_rebuild.py', 'MATCHING.md', 'match_function.py', 'tests/test_match_function.py', 'tests/test_vu_learning.py', 'patches/cdvdfsv-resident.patch', 'tests/test_cdvdfsv_resident.py', 'patches/sif-module-load-report.patch', 'tests/test_sif_module_report.py', 'patches/iop-module-buffer.patch', 'tests/test_iop_module_buffer.py', 'patches/dma-watch.patch', 'tests/test_dma_watch.py', 'patches/mpeg-stream-types.patch', 'tests/test_mpeg_stream_types.py','patches/gs-image-block-units.patch','tests/test_gs_image_block_units.py','patches/vif1-direct-image-span.patch','tests/test_vif1_direct_image_span.py','patches/mpeg-title-owned-input.patch','patches/native-frame-effects.patch','runtime/presentation/frame_effects.hpp','runtime/presentation/FSR1-LICENSE.txt','patches/source-chain-scratchpad.patch','patches/native-mac-surface.patch','generated_corrections.py','tests/test_generated_corrections.py','tests/frame_effects.cpp','tests/test_frame_effects.py')


SOURCE_FILES = ('translation_graph.py','tests/test_translation_graph.py') + SOURCE_FILES


SOURCE_FILES = ('runtime_header_replay.py','tests/test_runtime_header_replay.py') + SOURCE_FILES


SOURCE_FILES = ('source_inventory.py','tests/test_source_inventory.py','tests/test_source_reuse.py','asset_intake.py','tests/test_asset_intake.py') + SOURCE_FILES

def package_source(root=ROOT):
    """Explicit public-source whitelist; never recurse into game/work directories."""
    root = Path(root).resolve()
    target = root.parent / 'PS2-Port-Lab.zip'
    temp = target.with_suffix('.zip.tmp')
    for name in SOURCE_FILES:
        p = root / name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
            raise ValueError(f'Missing or unsafe source package member: {name}')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            archive.write(root / name, 'ps2-port-lab/' + name)
    with zipfile.ZipFile(temp) as archive:
        if archive.namelist() != ['ps2-port-lab/' + n for n in SOURCE_FILES] or archive.testzip():
            raise RuntimeError('Source archive validation failed')
    temp.replace(target)
    print(f'Validated {len(SOURCE_FILES)} public-source files: {target}')
    return target


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def safe_relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or '..' in path.parts or '\\' in value:
        raise ValueError(f'Unsafe relative path: {value!r}')
    return path


PRIVATE_ROOT = ROOT / '.private'
PROFILE_COMMANDS_WITHOUT_PROFILE = {'compare-source-objects','merge-source-graphs','revise-source-graph','recover-compile-settings','audit-archive','stage-source-graph','analyze-field-trace','audit-build','build-source','convert-disc','qualify-port','review-upstreams','doctor', 'bootstrap', 'bootstrap-runtime', 'source-package',
                                    'knowledge-add', 'package-accepted-app', 'new-profile', 'instruction-check', 'oracle-check'}


def private_directory(profile, root=ROOT):
    """Resolve a profile's private game storage without silently leaving the checkout.

    Default: <root>/.private/games/<id>. A relative private_dir must stay under
    <root>/.private. An existing project elsewhere needs an absolute private_dir
    plus "private_dir_absolute_opt_in": true; it may not be inside the reusable
    source tree (outside .private) or contain the source tree.
    """
    root = Path(root).resolve()
    private_root = (root / '.private').resolve()
    value = profile.get('private_dir')
    if value is None:
        resolved = (private_root / 'games' / profile['id']).resolve()
    elif not isinstance(value, str) or not value:
        raise ValueError('Profile private_dir must be a nonempty string')
    elif Path(value).is_absolute():
        if profile.get('private_dir_absolute_opt_in') is not True:
            raise ValueError('An absolute private_dir requires "private_dir_absolute_opt_in": true in the profile')
        resolved = Path(value).resolve()
    else:
        resolved = (root / safe_relative(value)).resolve()
        if not resolved.is_relative_to(private_root):
            raise ValueError('A relative private_dir must stay under .private/; for an existing project elsewhere '
                             'use an absolute path with "private_dir_absolute_opt_in": true')
    if value is None or not Path(value).is_absolute():
        # Default/relative storage is only safe while it really stays inside this
        # checkout. A symlinked .private (or a symlink below it) that resolves
        # elsewhere needs the explicit absolute private_dir opt-in instead.
        if not resolved.is_relative_to(root):
            raise ValueError('.private storage resolves outside the source tree (symlink?); use an absolute '
                             'private_dir with "private_dir_absolute_opt_in": true for storage elsewhere')
        if private_root != root / '.private':
            raise ValueError('.private storage root must not redirect into a public source directory')
    if resolved == Path('/') or root.is_relative_to(resolved):
        raise ValueError('Private game directory cannot contain the source tree')
    if resolved.is_relative_to(root) and not resolved.is_relative_to(root / '.private'):
        raise ValueError('Private game directory inside the source tree must be under .private/')
    return resolved


def load_profile(path):
    if path is None:
        raise ValueError('This command needs an explicit per-game --profile (see profiles/example-game.json)')
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f'Profile not found: {path}; create one with new-profile or next-game --title/--source')
    profile = json.loads(path.read_text())
    if not isinstance(profile, dict):
        raise ValueError('Profile must be a JSON object')
    if not isinstance(profile.get('id'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', profile['id']):
        raise ValueError('Profile id must contain lowercase letters, digits, hyphens or underscores')
    return profile, private_directory(profile)


def boot_executable(root, profile):
    if profile.get('expected_executable'):
        rel = safe_relative(profile['expected_executable'])
    else:
        text = (root / 'SYSTEM.CNF').read_text(encoding='ascii', errors='replace')
        m = re.search(r'^\s*BOOT2\s*=\s*cdrom\d?:[\\/]([^;\r\n]+);\d+', text, re.M | re.I)
        if not m:
            raise ValueError('Cannot identify BOOT2; set expected_executable in the game profile')
        rel = safe_relative(m[1].replace('\\', '/'))
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('Executable escapes disc directory')
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def elf_info(path):
    data = Path(path).read_bytes()
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected a 32-bit little-endian ELF v1')
    h = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    if h[1] != 8 or h[0] != 2:
        raise ValueError('Expected an executable MIPS ELF')

    def span(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('ELF table or section exceeds file bounds')
        return data[offset:offset + size]

    if h[11] and h[10] < 40:
        raise ValueError('Invalid ELF section entry size')
    span(h[5], h[10] * h[11])
    table = [struct.unpack_from('<IIIIIIIIII', data, h[5] + i * h[10]) for i in range(h[11])]
    if table and h[12] >= len(table):
        raise ValueError('Invalid section-name table index')
    names = span(table[h[12]][4], table[h[12]][5]) if table else b''

    def cstring(blob, offset):
        if offset >= len(blob):
            raise ValueError('ELF string index exceeds table')
        return blob[offset:].split(b'\0', 1)[0].decode('utf-8', errors='replace')

    sections = []
    functions = []
    for s in table:
        name = cstring(names, s[0])
        sections.append({'name': name, 'type': s[1], 'address': hex(s[3]), 'offset': s[4], 'bytes': s[5]})
        if s[1] == 8:  # SHT_NOBITS is not backed by file bytes.
            continue
        span(s[4], s[5])
        if s[1] != 2:
            continue
        if s[6] >= len(table) or s[9] < 16 or s[5] % s[9]:
            raise ValueError('Invalid symbol table')
        strings = span(table[s[6]][4], table[s[6]][5])
        for off in range(s[4], s[4] + s[5], s[9]):
            nameoff, value, size, info, other, index = struct.unpack_from('<IIIBBH', data, off)
            if info & 15 == 2 and index != 0:
                functions.append({'name': cstring(strings, nameoff), 'address': hex(value), 'bytes': size, 'section': index})
    clues = sorted(set(m.group().decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{8,}', data)
                       if re.search(rb'gcc2_compiled|Metrowerks|CodeWarrior|SN Systems|ProDG|/sce/ee/gcc', m.group(), re.I)))
    return {'entry': hex(h[3]), 'sha256': digest(path), 'bytes': len(data), 'sections': sections,
            'defined_function_count': len(functions), 'functions': functions,
            'compiler_evidence': clues, 'exact_compiler_version': None,
            'architecture': {'machine': 'mips', 'elf_class': 32, 'byte_order': 'little',
                'elf_flags': hex(h[6]),
                'r5900_machine_flag': (h[6] & 0x00ff0000) == 0x00920000,
                'ghidra_language_candidate': 'r5900:LE:32:default' if (h[6] & 0x00ff0000) == 0x00920000 else None,
                'scope': 'ELF header evidence only; not instruction validation or automatic REA activation. '
                         'Unmarked MIPS executables require independent ISA/ABI confirmation.'}}


def import_disc(source, profile, private):
    source = Path(source).resolve()
    dest = private / 'disc-root'
    if dest.resolve().is_relative_to(source) or source.is_relative_to(dest.resolve()):
        raise ValueError('Source and destination directories must not contain each other')
    oldpath = private / 'disc-manifest.json'
    old = json.loads(oldpath.read_text()) if oldpath.exists() else {}
    source_elf = boot_executable(source, profile)
    source_sha = digest(source_elf)
    if profile.get('expected_sha256') and source_sha != profile['expected_sha256']:
        raise ValueError('Source executable differs from profile; use the correct disc/profile')
    identity = {'executable': str(source_elf.relative_to(source)), 'sha256': source_sha}
    if old.get('identity') and old['identity'] != identity:
        raise ValueError('Source disc identity changed; create a separate profile')
    prior_elf = dest / identity['executable']
    if prior_elf.exists() and prior_elf.stat().st_size > 0 and digest(prior_elf) != source_sha:
        raise ValueError('Existing executable belongs to a different input; create a separate profile')
    known = {r['path']: r for r in old.get('files', []) if r['status'] == 'copied'}
    paths = sorted(source.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Disc input must not contain symlinks')
    files = [p for p in paths if p.is_file()]
    if not files:
        raise ValueError('No files found; is the disc mounted?')
    records = []
    for i, p in enumerate(files, 1):
        rel = str(p.relative_to(source))
        target = dest / safe_relative(rel)
        previous = known.get(rel)
        size = p.stat().st_size
        if previous and previous['bytes'] == size and target.exists() and digest(target) == previous['sha256'] and digest(p) == previous['sha256']:
            records.append(previous)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.partial')
            h = hashlib.sha256()
            try:
                with p.open('rb') as r, temp.open('wb') as w:
                    while block := r.read(1024 * 1024):
                        w.write(block)
                        h.update(block)
                if temp.stat().st_size != size or digest(temp) != h.hexdigest():
                    raise OSError('Copy size or checksum mismatch')
                temp.replace(target)
            except OSError as e:
                save(oldpath, {'source': str(source), 'identity': identity, 'complete': False, 'files': records,
                               'error': {'path': rel, 'message': str(e)}})
                raise
            records.append({'path': rel, 'bytes': size, 'sha256': h.hexdigest(), 'status': 'copied'})
        save(oldpath, {'source': str(source), 'identity': identity, 'complete': len(records) == len(files), 'expected_file_count': len(files), 'files': records})
        print(f'[{i}/{len(files)}] {rel}', flush=True)
    print(f'Imported {len(files)} files; local-only directory copy complete')


def verify(profile, private):
    manifest = json.loads((private / 'disc-manifest.json').read_text())
    root = private / 'disc-root'
    errors = []
    if not manifest.get('files'):
        raise ValueError('Empty manifest')
    for r in manifest['files']:
        path = root / safe_relative(r['path'])
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError('Manifest path escapes disc directory')
        if r['status'] != 'copied' or not path.exists() or path.stat().st_size != r['bytes'] or digest(path) != r.get('sha256'):
            errors.append(r['path'])
    if manifest.get('complete') is False:
        errors.append('Manifest records an incomplete import')
    if manifest.get('expected_file_count', len(manifest['files'])) != len(manifest['files']):
        errors.append('Manifest file count mismatch')
    if errors:
        raise ValueError(f'Verification failed: {errors[:10]}')
    elf = boot_executable(root, profile)
    if profile.get('expected_sha256') and digest(elf) != profile['expected_sha256']:
        raise ValueError('Executable differs from profile fingerprint')
    save(private / 'verification.json', {'verified_at': time.time(), 'file_count': len(manifest['files']),
         'bytes': sum(r['bytes'] for r in manifest['files']), 'executable_sha256': digest(elf)})
    print(f'Verified all {len(manifest["files"])} files locally')


def inspect(profile, private):
    elf = boot_executable(private / 'disc-root', profile)
    info = elf_info(elf)
    save(private / 'analysis/elf.json', info)
    print(f'{elf.name}: {info["defined_function_count"]} defined function symbols; entry {info["entry"]}')
    print('Compiler strings are evidence only; exact compiler version remains unverified')
    return elf


def run(command, log, offline=False, cwd=None):
    command = [str(c) for c in command]
    policy_note = ''
    if offline:
        permitted = {str(BUILD / 'ps2xAnalyzer/ps2_analyzer'), str(BUILD / 'ps2xRecomp/ps2_recomp')}
        if command[0] not in permitted:
            raise RuntimeError('Unrecognized tool for private game processing')
        revision = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != LOCK['ps2recomp']['revision']:
            raise RuntimeError('Tool source revision differs from lock')
        from tool_state import ensure
        ensure(sys.modules[__name__])
        for directory in [SOURCE / 'ps2xAnalyzer/src', SOURCE / 'ps2xRecomp/src']:
            for source in directory.rglob('*'):
                if source.suffix in {'.cpp', '.c', '.h', '.hpp'} and NETWORK_APIS.search(source.read_text(errors='replace')):
                    raise RuntimeError(f'Source requires a network/process API review: {source}')
        # Nested sandboxing can be rejected by the host sandbox. Be explicit:
        # source review is a precaution, not equivalent to OS network isolation.
        sandbox = Path('/usr/bin/sandbox-exec')
        if sys.platform == 'darwin' and sandbox.exists():
            prefix = [str(sandbox), '-p', '(version 1)(allow default)(deny network*)']
            probe = subprocess.run([*prefix, '/usr/bin/true'], capture_output=True)
            if probe.returncode == 0:
                command = [*prefix, *command]
                policy_note = 'OS network-deny sandbox enabled.\n'
            else:
                policy_note = 'Nested OS sandbox unavailable. Running reviewed local file tools; OS network isolation is not enforced by this wrapper.\n'
        else:
            policy_note = 'Running reviewed local file tools; OS network isolation is not available on this platform.\n'
        print(policy_note.strip(), flush=True)
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as f:
        f.write(policy_note)
        f.flush()
        result = subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
    if result.returncode:
        raise RuntimeError(f'Tool returned {result.returncode}; see local log {log}')


def bootstrap():
    # Only public tool identifiers enter these network-enabled commands.
    WORK.mkdir(parents=True, exist_ok=True)
    if not ENV.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ENV)], check=True)
    bindir = ENV / ('Scripts' if os.name == 'nt' else 'bin')
    packages = [f'{k}=={v}' for k, v in LOCK['python_build_tools'].items()]
    run([bindir / 'python', '-m', 'pip', 'install', '--no-cache-dir', *packages], WORK / 'build-logs/python-tools.log')
    ref = LOCK['ps2recomp']
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', ref['url'], str(SOURCE)], check=True)
    current = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if current != ref['revision']:
        if subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain'], text=True).strip():
            raise RuntimeError('Tool source has local changes; refusing to change its revision')
        subprocess.run(['git', '-C', str(SOURCE), 'fetch', 'origin', ref['revision']], check=True)
        subprocess.run(['git', '-C', str(SOURCE), 'checkout', '--detach', ref['revision']], check=True)
    from native import apply_runtime_patch
    apply_runtime_patch()
    from tool_state import configure_command
    run(configure_command(sys.modules[__name__]), WORK / 'build-logs/configure.log')
    for name, expected in LOCK.get('resolved_dependencies', {}).items():
        source = BUILD / '_deps' / f'{name}-src'
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != expected:
            raise RuntimeError(f'Dependency {name} differs from the locked revision')
    from tool_state import inputs as compiler_inputs,record as record_compiler
    expected_compiler_inputs=compiler_inputs(sys.modules[__name__])
    run([bindir / 'cmake', '--build', BUILD, '--target', 'ps2_analyzer', 'ps2_recomp', '-j', '4'], WORK / 'build-logs/build.log')
    record_compiler(sys.modules[__name__],expected_compiler_inputs)
    print('Pinned analyzer and recompiler built; no game data used during setup')


def analyze(profile, private):
    elf = inspect(profile, private)
    config = private / 'analysis/recomp.toml'
    (private / 'analysis/analyzer-stage.json').unlink(missing_ok=True)
    (private / 'analysis/translation-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-syntax-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-build-stage.json').unlink(missing_ok=True)
    (private / 'analysis/boot-probe-stage.json').unlink(missing_ok=True)
    analyzer = BUILD / 'ps2xAnalyzer/ps2_analyzer'
    run([analyzer, elf, config], private / 'logs/analyze.log', offline=True, cwd=private)
    # Rewrite only path/output/diagnostic settings. Preserve analyzer decisions.
    text = config.read_text()
    for key, value in {'input': json.dumps(str(elf)), 'output': json.dumps(str(private / 'generated')),
                       'single_file_output': 'false', 'low_memory_mode': 'true', 'output_worker_threads': '1'}.items():
        if re.search(rf'^\s*{key}\s*=', text, re.M):
            text = re.sub(rf'^\s*{key}\s*=.*$', lambda m: f'{key} = {value}', text, flags=re.M)
        else:
            text = text.replace('[general]', f'[general]\n{key} = {value}', 1)
    config.write_text(text)
    # Stripped executables: the analyzer can merge a function only reached through a pointer into its
    # predecessor. Add the generic pointer-entry hints here so every title gets them without a manual
    # step and this stage's recorded output is the final configuration. Profile opt-out:
    # "pointer_entry_hints": false.
    pointer_hints = None
    if profile.get('pointer_entry_hints', True) is not False:
        from pointer_entries import apply as apply_pointer_hints
        report = apply_pointer_hints(profile, private, write_config=True)
        pointer_hints = {'candidates': len(report['candidates']), 'counts': report['counts'],
                         'already_known': report['already_known'], 'config_updated': report['config_updated']}
    save(private / 'analysis/analyzer-stage.json', {'success': True, 'config': str(config), 'tool_revision': LOCK['ps2recomp']['revision'],
                                                    'pointer_entry_hints': pointer_hints})
    print('Analyzer finished; configuration saved locally')


CPP_SUFFIXES = {'.cpp', '.cc', '.cxx', '.c++'}


def generated_sources(directory):
    """The one generated-C++ inventory shared by translation, knowledge, syntax checks and builds.

    Every consumer (including cmake/AttachGame.cmake) reads top-level *.cpp only, so nested
    or differently suffixed C++ fails closed instead of being silently skipped.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError('No generated source directory; run translate first')
    top, unsupported = [], []
    from clean_build import filesystem_metadata
    for path in directory.rglob('*'):
        if path.suffix.lower() not in CPP_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError(f'Generated C++ must not be a symlink: {path.name}')
        if path.is_file() and filesystem_metadata(path):
            continue
        if path.parent == directory and path.suffix == '.cpp':
            top.append(path)
        else:
            unsupported.append(str(path.relative_to(directory)))
    if unsupported:
        raise ValueError(f'Nested or non-.cpp generated C++ is not supported by all consumers: {sorted(unsupported)[:5]}')
    return sorted(top)


SUMMARY = re.compile(r'Functions processed: (\d+), recompiled: (\d+), stubs: (\d+), skipped: (\d+), decode failures: (\d+)')
DIAGNOSTICS = re.compile(r'Warnings: (\d+), errors: (\d+)')


def translation_acceptance(report):
    """Parse the recompiler summary. Missing/unrecognized summaries and reported errors fail closed.

    Stubs and skipped functions are reported separately: they are not implementations.
    """
    counts, blockers = {}, []
    summary, diagnostics = SUMMARY.search(report), DIAGNOSTICS.search(report)
    if summary:
        counts.update(zip(['processed', 'recompiled', 'stubs', 'skipped', 'decode_failures'], map(int, summary.groups())))
    else:
        blockers.append('recompiler function summary missing or unrecognized')
    if diagnostics:
        counts.update(warnings=int(diagnostics[1]), errors=int(diagnostics[2]))
    else:
        blockers.append('recompiler warning/error summary missing or unrecognized')
    if counts.get('decode_failures'):
        blockers.append(f"{counts['decode_failures']} instruction decode failures")
    if counts.get('errors'):
        blockers.append(f"{counts['errors']} recompiler errors")
    return dict(counts=counts, accepted=not blockers, blockers=blockers,
                stub_functions=counts.get('stubs'), skipped_functions=counts.get('skipped'),
                stubs_are_implementations=False)


def require_accepted_translation(private):
    path = Path(private) / 'analysis/translation-stage.json'
    if not path.is_file():
        raise ValueError('Run translate first')
    stage = json.loads(path.read_text())
    if stage.get('accepted') is not True:
        raise ValueError('Translation was not accepted: ' + '; '.join(stage.get('blockers') or ['legacy record without acceptance; retranslate']))
    generated=Path(private)/'generated'
    if generated.is_dir():
        from sdk_policy import audit_generated
        if not audit_generated(generated)['passed']:raise ValueError('Generated output still contains incomplete stateful SDK substitutions; retranslate with current compiler policy')
    return stage


def correct_generated_sqrt(directory):
    """Compatibility entry point backed by the executable knowledge bank."""
    from knowledge import apply_translation
    return apply_translation(directory, only={'ee-sqrt-ft'})['totals']


def translate(profile, private, feedback=None):
    config = private / 'analysis/recomp.toml'
    if not config.exists():
        raise ValueError('Run analyze first')
    feedback_receipt = None
    if feedback is not None:
        from feedback import consume
        config, feedback_receipt = consume(profile, private, feedback)
    from resident_closure import configuration as resident_configuration
    config, resident_entries = resident_configuration(profile, private, config)
    (private / 'analysis/translation-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-syntax-stage.json').unlink(missing_ok=True)
    (private / 'analysis/native-build-stage.json').unlink(missing_ok=True)
    (private / 'analysis/boot-probe-stage.json').unlink(missing_ok=True)
    # Retain old output for debugging, but do not count stale source as new output.
    output = private / 'generated'
    if output.exists():
        archive = private / 'previous-translations' / str(time.time_ns())
        archive.parent.mkdir(parents=True, exist_ok=True)
        output.rename(archive)
    run([BUILD / 'ps2xRecomp/ps2_recomp', config], private / 'logs/translate.log', offline=True, cwd=private)
    from knowledge import apply_translation
    learned = apply_translation(private/'generated', identity=digest(boot_executable(private/'disc-root',profile)))
    corrections = learned['totals']
    save(private/'analysis/knowledge-translation.json', learned)
    from generated_corrections import apply as apply_generated_corrections
    apply_generated_corrections(profile, private)
    save(private / 'analysis/sqrt-translation-corrections.json', corrections)
    files = generated_sources(private / 'generated')
    if not files:
        raise RuntimeError('Tool exited successfully but emitted no C++ files')
    acceptance = translation_acceptance((private / 'logs/translate.log').read_text(errors='replace'))
    from sdk_policy import audit_generated
    sdk=audit_generated(output)
    save(private/'analysis/sdk-policy-stage.json',sdk)
    if not sdk['passed']:
        acceptance['accepted']=False
        acceptance['blockers'].append('Incomplete stateful SDK substitutions remain in generated output')
    save(private / 'analysis/translation-stage.json', {'success': acceptance['accepted'], **acceptance,
         'cpp_files': len(files), 'cpp_bytes': sum(p.stat().st_size for p in files),
         'generated_sources': {p.name:digest(p) for p in files},
         'translation_rule_implementation': __import__('knowledge').translation_implementation(),
         'tool_revision': LOCK['ps2recomp']['revision'],
         'native_build_verified': False, 'runtime_verified': False,
         'runtime_target_feedback': feedback_receipt, 'overlay_resident_entries': resident_entries,
         'knowledge_bank_revision': learned['bank_revision'], 'knowledge_bank_sha256': learned['bank_sha256']})
    counts = acceptance['counts']
    print(f'Translation emitted {len(files)} C++ files; {counts.get("stubs", "unknown")} stub and '
          f'{counts.get("skipped", "unknown")} skipped functions are not implementations.')
    if not acceptance['accepted']:
        raise RuntimeError('Translation not accepted; downstream native stages are blocked: ' + '; '.join(acceptance['blockers']))
    print('Native build and gameplay remain unverified.')


def syntax_check_command(*include_dirs):
    """clang++ syntax-only command for generated C++ against the runtime headers."""
    compiler = shutil.which('clang++')
    if not compiler:
        raise RuntimeError('Native clang++ is missing')
    flags = [compiler, '-std=c++20', '-fsyntax-only', '-ferror-limit=8',
             '-I', str(SOURCE / 'ps2xRuntime/include'), '-I', str(SOURCE / 'ps2xRuntime/src/lib/Kernel'),
             '-I', str(SOURCE / 'ps2xIOP/include')]
    for directory in include_dirs:
        flags += ['-I', str(directory)]
    if platform.machine() == 'arm64':
        flags += ['-DUSE_SSE2NEON', '-I', str(BUILD / '_deps/sse2neon-src')]
    return flags


def check_generated(profile, private):
    """Check representative generated source with the native compiler, not a game build."""
    directory = private / 'generated'
    require_accepted_translation(private)
    files = generated_sources(directory)
    if not files:
        raise ValueError('Run translate first')
    # Spread a general sample across the inventory and include largest functions.
    sample = {files[0], files[-1], *files[::max(1, len(files) // 8)]}
    sample.update(sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:2])
    if profile.get('generated_source_corrections'):
        receipt_path = private / 'analysis/generated-correction-stage.json'
        if not receipt_path.is_file():
            raise ValueError('Apply pinned title corrections before checking their generated source')
        correction_receipt = json.loads(receipt_path.read_text())
        from generated_corrections import verify as verify_corrections
        verify_corrections(profile, private, correction_receipt)
        # Every retained title edit is material; a representative sample can miss
        # exactly the camera/menu/photo correction this build is meant to carry.
        sample.update(directory / row['file'] for row in correction_receipt['files'] if row['file'].endswith('.cpp'))
    flags = syntax_check_command(directory)
    records = []
    for p in sorted(sample):
        log = private / 'logs/native-syntax' / (p.name + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w') as f:
            result = subprocess.run([*flags, str(p)], stdout=f, stderr=subprocess.STDOUT)
        records.append({'file': p.name, 'returncode': result.returncode, 'log': str(log)})
        print(f'{"PASS" if result.returncode == 0 else "FAIL"} {p.name}', flush=True)
    save(private / 'analysis/native-syntax-stage.json', {'sample_size': len(records), 'files': records,
         'success': all(r['returncode'] == 0 for r in records), 'full_native_build_verified': False})
    if any(r['returncode'] for r in records):
        raise RuntimeError('Representative generated source failed native syntax checks; inspect local logs')
    print(f'{len(records)} representative files passed; full build and runtime remain unverified')


def status(profile, private):
    print(f'{profile["title"]}: {private}')
    for stage, file in [('Verification', 'verification.json'), ('Inspection', 'analysis/elf.json'),
                        ('Analyzer', 'analysis/analyzer-stage.json'), ('Translation', 'analysis/translation-stage.json'),
                        ('Native syntax sample', 'analysis/native-syntax-stage.json'),
                        ('Native build', 'analysis/native-build-stage.json'), ('First boot probe', 'analysis/boot-probe-stage.json')]:
        print(stage + ': ' + ('recorded (see private artifact)' if (private / file).exists() else 'not recorded'))
    print('Runtime: ' + profile.get('runtime_status', 'not_integrated'))


def main(target=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Explicit per-game profile JSON (required for game processing)')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ['doctor', 'bootstrap', 'bootstrap-runtime', 'build-native', 'test-runtime', 'boot-probe', 'diagnose', 'package-local', 'verify', 'inspect', 'code-census', 'translate-overlays', 'audit-reference-overlays', 'analyze', 'check-generated', 'prepare', 'status']:
        command_parser = sub.add_parser(cmd)
        if cmd == 'package-local':
            command_parser.add_argument('--output-directory', help='Absolute local directory for a new isolated app (for example an external drive)')
    translation = sub.add_parser('translate')
    translation.add_argument('--feedback', help='Explicit private runtime-feedback run name; revalidates fingerprints')
    feedback = sub.add_parser('collect-runtime-targets', help='Turn local missing-target logs into bounded main-ELF compilation hints')
    feedback.add_argument('--log', required=True, help='Path relative to private game storage')
    feedback.add_argument('--name', required=True)
    sub.add_parser('import').add_argument('source')
    preserved=sub.add_parser('import-preserved-copy',help='Import exactly a pinned original extracted-disc file set; leave saves and test extras at source')
    for option in ('source','manifest','sha256'):preserved.add_argument('--'+option,required=True)
    sub.add_parser('import-image', help='Copy, trim and verify a sector image (raw disc device or .iso) against the disc manifest').add_argument('source')
    pointers = sub.add_parser('scan-pointer-entries', help='Optional pre-build scan: pointer-only function starts as entry_points hints')
    pointers.add_argument('--report-only', action='store_true', help='Write the hint report without changing recomp.toml')
    sub.add_parser('source-package')
    oracle = sub.add_parser('oracle-check', help='Run fixed expected integer/RAM original-execution regressions')
    recovery = sub.add_parser('recover-function', help='Recover, compile and compare a bounded integer leaf against original instructions')
    recovery.add_argument('--spec', required=True, help='Private recovery specification relative to game storage')
    recovery.add_argument('--name', required=True)
    recovery.add_argument('--bank', help='Private relative bank path, or @shared for the local cross-title bank')
    recovery.add_argument('--reuse', help='Explicit content-hashed bank candidate; always reverified against target')
    recovery.add_argument('--bindings', help='Private JSON of explicit old/new complete address constants')
    find_recovery = sub.add_parser('find-recovery-candidates', help='Find exact/masked private code candidates without accepting reuse')
    find_recovery.add_argument('--spec', required=True)
    find_recovery.add_argument('--bank', required=True)
    check = sub.add_parser('instruction-check', help='Compile scalar FPU result checks against published hardware vectors')
    check.add_argument('--backend', choices=['scalar','reference','legacy'], default='scalar')
    check.add_argument('--allow-known-rounding', action='store_true', help='Explicitly qualify documented one-step rounding differences')
    check.add_argument('--report', help='New local JSON report path; existing files are not overwritten')
    sub.add_parser('knowledge-plan', help='Detect reusable contracts and run configured knowledge gates')
    sub.add_parser('enhancement-plan', help='Schedule every graphics/game/runtime stage; missing evidence cannot pass')
    freeze = sub.add_parser('freeze-source', help='Rebuild and freeze a unit using actual compiler-discovered dependencies')
    for option in ['compile-db','unit','source','reference','name']:
        freeze.add_argument('--'+option, required=True)
    bank = sub.add_parser('knowledge-add', help='Append a data-only rule after regression validation')
    bank.add_argument('--bank', default=str(ROOT/'knowledge.json'))
    bank.add_argument('--candidate', required=True)
    next_game = sub.add_parser('next-game', help='Run offline next-title intake/translation and report integration gaps')
    next_game.add_argument('--source', help='Mounted disc directory; omit after import')
    next_game.add_argument('--title', help='Create a missing profile, discover BOOT2 and pin the executable revision')
    next_game.add_argument('--name', required=True)
    next_game.add_argument('--image', help='Raw disc device or .iso; intake also produces a verified sector image')
    next_game.add_argument('--through', choices=('intake', 'translate'), default='intake')
    next_game.add_argument('--execute', action='store_true', help='Execute fixed local stages; default only plans')
    next_game.add_argument('--resume', action='store_true')
    release = sub.add_parser('package-accepted-app', help='Wrap a preserved accepted GPU build in a normal local Mac app')
    for option in ['source', 'destination', 'title', 'version', 'bundle-id', 'icon', 'log']:
        release.add_argument('--'+option, required=True)
    release.add_argument('--bundle-assets',action='store_true',help='Include verified disc/texture assets; require source-verified separate saves')
    matching = sub.add_parser('match-function', help='Compile one function with a pinned PS2 compiler and compare it with the disc')
    matching.add_argument('--function', required=True)
    matching.add_argument('--source', required=True, help='C/C++ source inside private game storage')
    matching.add_argument('--compiler', required=True, help='Compiler id under the toolchain ps2-compilers folder')
    matching.add_argument('--name', required=True, help='Unique local match name')
    matching.add_argument('--flag', action='append', dest='flags', help='Compiler flag (repeatable; default -O2 -G0)')
    rebuild = sub.add_parser('rebuild-binary', help='Split the boot executable into assembly and rebuild it byte-for-byte')
    rebuild.add_argument('--name', required=True, help='Unique local rebuild name')
    rebuild.add_argument('--gp', type=lambda v: int(v, 0), help='gp value if the startup code does not show it')
    memory = sub.add_parser('memory-audit', help='Reject runtime pools overlapping declared game memory')
    memory.add_argument('--runtime-header', required=True, help='Local runtime Helpers/State.h used for this build')
    memory.add_argument('--layout', required=True, help='Private JSON layout path, relative to game storage')
    memory.add_argument('--name', required=True, help='Unique local audit name')
    for command in ['compiler-plan', 'compiler-stage']:
        recipe = sub.add_parser(command, help='Validate a pinned local compiler recipe; optionally stage isolated sources')
        recipe.add_argument('--recipe', required=True, help='Recipe JSON inside private game storage')
        recipe.add_argument('--name', required=True, help='Unique local compiler run name')
    sub.add_parser('compiler-toolchain', help='Pin the selected local macOS compiler/linker/SDK').add_argument('--name', required=True)
    adapters = sub.add_parser('stage-adapters', help='Stage guarded title-specific module copies; never alter the disc')
    adapters.add_argument('--manifest',required=True)
    adapters.add_argument('--name',required=True)
    textures = sub.add_parser('enhance-textures', help='Process private decoded textures locally with a verified cache')
    textures.add_argument('--manifest', required=True)
    textures.add_argument('--name', required=True)
    sub.add_parser('compile-vu').add_argument('capture',help='Captured vector-code directory inside private game storage')
    dep = sub.add_parser('analyze-vu-dependencies', help='Describe modeled VU entry inputs without enabling optimizations')
    dep.add_argument('capture', help='Captured VU1 image directory inside private game storage')
    dep.add_argument('--name', required=True)
    replay = sub.add_parser('replay-raw-gs', help='Replay a private capture through a pinned local backend')
    replay.add_argument('capture', help='Capture path relative to private game storage')
    replay.add_argument('--backend', required=True, help='Private pinned backend JSON path')
    replay.add_argument('--name', required=True, help='Unique local run name')
    replay.add_argument('--batch', choices=('on', 'off'), default='on')
    replay.add_argument('--timeout', type=int, default=300)
    cadence = sub.add_parser('analyze-cadence', help='Separate host presentation, emulated speed and fresh-image cadence')
    cadence.add_argument('capture', help='Telemetry CSV inside private game storage')
    cadence.add_argument('--name', required=True)
    cadence.add_argument('--expected-field-hz', type=float, default=60000/1001)
    scenario = sub.add_parser('scenario')
    scenario.add_argument('definition', help='JSON definition inside the private game directory')
    scenario.add_argument('--name', required=True, help='Unique local run name')
    scenario.add_argument('--renderer-worker', choices=('off','on'), help='Recorded renderer treatment; scene definition and inputs stay identical')
    scenario.add_argument('--graphics-history', choices=('off','on'), help='Recorded heavy graphics-trace treatment; scene inputs stay identical')
    comparison = sub.add_parser('compare-scenarios')
    comparison.add_argument('baseline', help='Baseline local run name')
    comparison.add_argument('candidate', help='Candidate local run name')
    comparison.add_argument('--name', required=True, help='Unique local comparison name')
    ab = sub.add_parser('ab-check', help='Build this title on two lab stacks in isolation and compare (regression evidence)')
    ab.add_argument('--name', required=True, help='Unique run name; output defaults to <work>/ab-checks/<name>')
    ab.add_argument('--baseline-lab', default=str(ROOT), help='Lab tree before the change (default: this lab)')
    ab.add_argument('--candidate-lab', default=str(ROOT), help='Lab tree with the change (default: this lab)')
    ab.add_argument('--baseline-patches', help='Directory of *.patch overlaid on the baseline lab (an applied-stack record works)')
    ab.add_argument('--candidate-patches', help='Directory of *.patch overlaid on the candidate lab')
    ab.add_argument('--baseline-knowledge', help='knowledge.json overlaid on the baseline lab')
    ab.add_argument('--candidate-knowledge', help='knowledge.json overlaid on the candidate lab')
    ab.add_argument('--scenario', help='Optional scenario JSON inside the title private directory, run on both sides')
    ab.add_argument('--boot-seconds', type=int, default=30, help='Headless boot gate limit')
    ab.add_argument('--reuse-generated', action='store_true', help='Copy the title generated code instead of retranslating (runtime-only changes)')
    ab.add_argument('--output', help='Output directory outside the title private directory and the lab')
    ab.add_argument('--allow-busy', action='store_true', help='Run even while a ps2EntryRunner is running elsewhere')
    ab.add_argument('--jobs', type=int, default=4)
    new = sub.add_parser('new-profile', help='Create a per-game profile under .private/profiles (never inside public profiles/)')
    new.add_argument('id')
    new.add_argument('--title', required=True)
    new.add_argument('--source', help='Mounted disc directory; pins BOOT2 executable and SHA-256')
    new.add_argument('--output', help='Profile path (default .private/profiles/<id>.json); filename must be <id>.json')
    convert = sub.add_parser('convert-disc', help='Automatically discover BOOT2, import, verify, analyze, translate and qualify a mounted disc')
    convert.add_argument('source')
    convert.add_argument('--id', required=True)
    convert.add_argument('--title', required=True)
    convert.add_argument('--name', default='conversion')
    convert.add_argument('--plan', action='store_true')
    convert.add_argument('--resume', action='store_true')
    convert.add_argument('--through', choices=('translate','native','package'))
    qualify = sub.add_parser('qualify-port', help='Audit real app, generated code, pinned receipts and runtime logs')
    qualify.add_argument('--app')
    qualify.add_argument('--receipt')
    qualify.add_argument('--log')
    qualify.add_argument('--generated')
    qualify.add_argument('--gif')
    qualify.add_argument('--acceptance')
    qualify.add_argument('--expected-audio-wav',action='append',help='Local decoded PCM WAV from a scene expected to be audible; silence fails, sound quality still requires review')
    qualify.add_argument('--require-scenario', action='append', default=[], help='Required reviewed scenario ID; repeat for each release acceptance gate')
    qualify.add_argument('--report', required=True)
    upstream = sub.add_parser('review-upstreams', help='Read public GitHub updates; never installs or activates code')
    upstream.add_argument('--repository', action='append', required=True)
    upstream.add_argument('--since', required=True)
    upstream.add_argument('--until', required=True)
    upstream.add_argument('--report', required=True)
    source_build = sub.add_parser('build-source', help='Build a hash-pinned source graph, rejecting historical objects/archives')
    source_build.add_argument('recipe')
    source_build.add_argument('--output', required=True)
    source_build.add_argument('--jobs', type=int, default=2)
    source_build.add_argument('--target', action='append', help='Build selected targets and their complete transitive source dependencies')
    source_build.add_argument('--unit', action='append', help='Compile only selected units without linking, for bounded source controls')
    source_build.add_argument('--execute', action='store_true', help='Without this flag, validate and plan only')
    source_build.add_argument('--reuse-build', help='Explicit incremental reuse of receipt-pinned source objects')
    source_build.add_argument('--reuse-recipe', help='Original pinned recipe for --reuse-build; complete source inventory must be unchanged')
    audit_build = sub.add_parser('audit-build', help='Classify native link inputs and derive clean-rebuild blockers')
    audit_build.add_argument('--link-argv', required=True, help='JSON array of actual native linker argv')
    audit_build.add_argument('--compile-db', required=True)
    audit_build.add_argument('--directory', required=True)
    audit_build.add_argument('--report', required=True)
    field_trace = sub.add_parser('analyze-field-trace', help='Detect completed-field stalls hidden behind smooth host swaps')
    field_trace.add_argument('capture')
    field_trace.add_argument('--seconds', type=float, default=60)
    field_trace.add_argument('--report', required=True)
    isolated = sub.add_parser('isolate-tool-sources', help='Reconstruct locked public tool sources from local Git objects without working-tree edits')
    isolated.add_argument('--output', required=True)
    relocated = sub.add_parser('relocate-source-graph', help='Move a verified source inventory to a fresh workspace without historical binaries')
    relocated.add_argument('recipe')
    relocated.add_argument('--staging', required=True)
    relocated.add_argument('--output', required=True)
    source_graph = sub.add_parser('stage-source-graph', help='Collect declared sources into a relocatable pinned build recipe')
    source_graph.add_argument('config')
    source_graph.add_argument('--output', required=True)
    archive = sub.add_parser('audit-archive', help='Resolve static archive members against exact recorded compiler outputs')
    archive.add_argument('archive')
    archive.add_argument('--compiler-record', action='append', default=[])
    archive.add_argument('--object-root', action='append', default=[], help='Search retained object bytes and suggest source candidates; never accepts equivalence')
    archive.add_argument('--report', required=True)
    sub.add_parser('bootstrap-native-graphics', help='Fetch only locked public source dependencies for the native hardware adapter')
    graphics = sub.add_parser('build-native-graphics', help='Build the hardware renderer/adapter from source without game assets or old objects')
    graphics.add_argument('--output', required=True)
    graphics.add_argument('--source', action='append', default=[], help='Existing locked public source: name=/absolute/path')
    graphics.add_argument('--execute', action='store_true')
    link_recipe = sub.add_parser('recover-link-target', help='Replace actual historical link inputs with pinned source-unit/target mappings')
    link_recipe.add_argument('argv')
    link_recipe.add_argument('--directory', required=True)
    link_recipe.add_argument('--mappings', required=True)
    link_recipe.add_argument('--name', required=True)
    link_recipe.add_argument('--report', required=True)
    abi = sub.add_parser('audit-abi-layouts', help='Compile shared type layout probes across pinned source components')
    abi.add_argument('config')
    abi.add_argument('--output', required=True)
    make_plan = sub.add_parser('recover-make-plan', help='Parse compiler/archive targets from a dry-run transcript without executing it')
    make_plan.add_argument('transcript')
    make_plan.add_argument('--directory', required=True)
    make_plan.add_argument('--report', required=True)
    selection = sub.add_parser('select-archive-sources', help='Resolve archive candidates using completed independent source controls')
    selection.add_argument('audit')
    selection.add_argument('--recipe', required=True)
    selection.add_argument('--build', required=True)
    selection.add_argument('--controls', required=True)
    selection.add_argument('--candidates', required=True)
    selection.add_argument('--report', required=True)
    controls = sub.add_parser('compare-source-objects', help='Compare completed source units against pinned historical object controls, never link them')
    controls.add_argument('recipe')
    controls.add_argument('--build', required=True)
    controls.add_argument('--references', required=True)
    controls.add_argument('--output', required=True)
    controls.add_argument('--native-text', action='store_true')
    merged = sub.add_parser('merge-source-graphs', help='Combine pinned source graphs and ledgers into one isolated build root')
    merged.add_argument('config')
    merged.add_argument('--output', required=True)
    merged.add_argument('--resume', action='store_true', help='Resume a failed source merge; changed existing source files are rejected')
    amended = sub.add_parser('amend-source-graph', help='Apply pinned literal source corrections in a new source graph')
    amended.add_argument('config')
    amended.add_argument('--output', required=True)
    retained = sub.add_parser('retain-source-corrections', help='Retain revision-pinned private source corrections for automatic replay')
    retained.add_argument('config')
    retained.add_argument('--bank', required=True)
    captured = sub.add_parser('capture-source-corrections', help='Capture every changed source file between pinned graphs for private replay')
    captured.add_argument('--executable', required=True)
    captured.add_argument('--before', required=True)
    captured.add_argument('--staging', required=True)
    captured.add_argument('--after', required=True)
    captured.add_argument('--output', required=True)
    captured.add_argument('--bank')
    corrections = sub.add_parser('apply-source-corrections', help='Select and replay a unique private correction set by executable/source identity')
    corrections.add_argument('--bank', required=True)
    corrections.add_argument('--executable', required=True)
    corrections.add_argument('--recipe', required=True)
    corrections.add_argument('--staging', required=True)
    corrections.add_argument('--output', required=True)
    revision = sub.add_parser('revise-source-graph', help='Revise pinned compiler settings and carry forward a matching staging ledger')
    revision.add_argument('recipe')
    revision.add_argument('--ledger', required=True)
    revision.add_argument('--updates', required=True)
    revision.add_argument('--recipe-output', required=True)
    revision.add_argument('--ledger-output', required=True)
    rebased = sub.add_parser('rebase-generated-corrections', help='Create an inactive three-way correction candidate for the same disc revision')
    rebased.add_argument('--previous-profile', required=True)
    rebased.add_argument('--bases', help='Optional private JSON mapping legacy baseline paths; default uses automatically retained pristine bytes')
    rebased.add_argument('--destination', required=True, help='New private relative candidate directory')
    recovered = sub.add_parser('recover-compile-settings', help='Recover declarative settings from a recorded compile database without executing it')
    recovered.add_argument('database')
    recovered.add_argument('--report', required=True)
    recovered.add_argument('--source-pch', action='store_true', help='Reconstruct only the exact recorded CMake PCH/source-header pair, never load old PCH binaries')
    args = parser.parse_args()
    # A next-game parent passes its resolved tool/work root. A mismatch means this
    # child would use different tools than the parent's resume contract pinned.
    expected = os.environ.get('PS2_PORT_LAB_EXPECTED_WORK')
    if expected and Path(expected).resolve() != WORK:
        raise RuntimeError(f'Work directory mismatch: parent pinned {expected}, child resolved {WORK}')
    if expected:
        print(f'[pipeline] work={WORK}', flush=True)
    if target is not None:
        from target_cli import check_target
        try: check_target(target, args.command)
        except ValueError as error: parser.error(str(error))
    if args.command=='bootstrap-native-graphics':
        from native_components import bootstrap
        print(json.dumps(bootstrap(sys.modules[__name__]),indent=2))
        return
    if args.command=='amend-source-graph':
        from graph_amend import amend
        report=amend(args.config,args.output)
        print(json.dumps(report,indent=2))
        return
    if args.command=='retain-source-corrections':
        from source_corrections import retain
        print(json.dumps(retain(args.config,args.bank),indent=2))
        return
    if args.command=='capture-source-corrections':
        from source_corrections import capture
        print(json.dumps(capture(args.executable,args.before,args.staging,args.after,args.output,bank=args.bank),indent=2))
        return
    if args.command=='apply-source-corrections':
        from source_corrections import apply
        print(json.dumps(apply(args.bank,args.executable,args.recipe,args.staging,args.output),indent=2))
        return
    if args.command=='build-native-graphics':
        from native_components import build
        sources={}
        for value in args.source:
            if '=' not in value:raise ValueError('Source override needs name=path')
            name,path=value.split('=',1)
            if name in sources:raise ValueError('Duplicate source override')
            sources[name]=path
        report=build(sys.modules[__name__],args.output,overrides=sources,execute=args.execute)
        print(json.dumps({k:v for k,v in report.items() if k not in {'engine_sources','commands'}},indent=2))
        return
    if args.command=='recover-link-target':
        from link_recipe import recover
        report=recover(json.loads(Path(args.argv).read_text()),args.directory,args.mappings,args.name)
        with Path(args.report).open('x') as f:json.dump(report,f,indent=2)
        print(json.dumps({k:v for k,v in report.items() if k not in {'mapped_inputs','recorded_argv'}},indent=2))
        if report['blocked']:raise SystemExit(1)
        return
    if args.command=='audit-abi-layouts':
        from abi_layouts import audit
        report=audit(args.config,args.output)
        print(json.dumps({k:v for k,v in report.items() if k!='probes'},indent=2))
        if report['status']=='abi_layouts_mismatch':raise SystemExit(1)
        return
    if args.command=='recover-make-plan':
        from make_plan import recover
        report=recover(args.transcript,args.directory)
        with Path(args.report).open('x') as f:json.dump(report,f,indent=2)
        print(json.dumps(dict(status=report['status'],units=len(report['units']),targets=len(report['targets']),blocked=report['blocked']),indent=2))
        if report['blocked']:raise SystemExit(1)
        return
    if args.command=='select-archive-sources':
        from archive_selection import select
        report=select(args.audit,args.recipe,args.build,args.controls,args.candidates,args.report)
        print(json.dumps({k:v for k,v in report.items() if k not in {'selected','unresolved','input_pins'}},indent=2))
        if report['unresolved']:raise SystemExit(1)
        return
    if args.command=='compare-source-objects':
        from object_controls import compare
        report=compare(args.recipe,args.build,args.references,args.output,native=args.native_text)
        print(json.dumps({k:v for k,v in report.items() if k!='objects'},indent=2))
        if report['status']=='controls_mismatch':raise SystemExit(1)
        return
    if args.command=='merge-source-graphs':
        from merge_graphs import merge
        report=merge(args.config,args.output,resume=args.resume)
        print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
        return
    if args.command=='revise-source-graph':
        from source_graph import revise
        report=revise(args.recipe,args.ledger,json.loads(Path(args.updates).read_text()),args.recipe_output,args.ledger_output)
        print(json.dumps(report,indent=2))
        return
    if args.command=='recover-compile-settings':
        from compile_recipe import recover
        report=recover(args.database,source_pch=args.source_pch)
        with Path(args.report).open('x') as f:json.dump(report,f,indent=2)
        print(json.dumps(dict(accepted=len(report['accepted']),blocked=len(report['blocked']),source_equivalence_verified=False)))
        return
    if args.command=='audit-archive':
        from archive_sources import audit as audit_archive,compiler_records,discover_records
        records=compiler_records(args.compiler_record)
        if args.object_root: records+=discover_records(args.archive,args.object_root)
        report=audit_archive(args.archive,records)
        with Path(args.report).open('x') as f: json.dump(report,f,indent=2)
        print(json.dumps({k:v for k,v in report.items() if k!='members'},indent=2))
        return
    if args.command=='isolate-tool-sources':
        from offline_toolchain import stage
        print(json.dumps(stage(sys.modules[__name__],args.output),indent=2))
        return
    if args.command=='relocate-source-graph':
        from source_graph import relocate
        print(json.dumps(relocate(args.recipe,args.staging,args.output),indent=2))
        return
    if args.command=='stage-source-graph':
        from source_graph import stage as stage_source_graph
        report=stage_source_graph(args.config,args.output)
        print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
        return
    if args.command=='analyze-field-trace':
        from cadence import analyze_field_trace
        report=analyze_field_trace(args.capture,args.seconds)
        with Path(args.report).open('x') as f: json.dump(report,f,indent=2)
        print(json.dumps(report,indent=2))
        return
    if args.command=='audit-build':
        from build_closure import audit as audit_build_inputs
        report=audit_build_inputs(json.loads(Path(args.link_argv).read_text()),args.compile_db,args.directory)
        with Path(args.report).open('x') as f: json.dump(report,f,indent=2)
        print(json.dumps(report,indent=2))
        return
    if args.command=='build-source':
        from clean_build import run as build_source
        report=build_source(args.recipe,args.output,jobs=args.jobs,execute=args.execute,only_units=args.unit,only_targets=args.target,reuse_build=args.reuse_build,reuse_recipe=args.reuse_recipe)
        print(json.dumps({k:v for k,v in report.items() if k not in {'commands','dependencies','toolchain_headers','objects'}},indent=2))
        return
    if args.command=='convert-disc':
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',args.id):
            raise ValueError('Use a lowercase title id')
        source=Path(args.source).resolve()
        if not source.is_dir():
            raise ValueError('Supply a mounted disc directory (mount ISO read-only first)')
        through=args.through or ('package' if target=='macos' else 'translate')
        if through in {'native','package'} and target!='macos':
            raise ValueError('Native build/package requires --target macos')
        from next_game import create_profile,run as convert_disc
        profile=ROOT/'.private/profiles'/(args.id+'.json')
        if not profile.exists():
            profile.parent.mkdir(parents=True,exist_ok=True)
            create_profile(profile,args.title,source)
        report=convert_disc(profile,args.name,source=source,through=through,
                            execute=not args.plan,resume=args.resume)
        print(json.dumps(report,indent=2))
        if report['status']=='blocked':
            raise RuntimeError('Conversion stopped at an explicit unsupported contract; inspect report ticket')
        return
    if args.command in {'qualify-port','review-upstreams'}:
        report_path=Path(args.report)
        if report_path.exists() or report_path.is_symlink():
            raise ValueError('Report already exists; preserve previous evidence')
        if args.command=='qualify-port':
            if not any([args.app,args.generated,args.log,args.gif,args.expected_audio_wav]):
                raise ValueError('Supply an app, generated directory or runtime log')
            from port_checks import audit
            report=audit(app=args.app,receipt=args.receipt,log=args.log,generated=args.generated,acceptance=args.acceptance,gif=args.gif,required_scenarios=args.require_scenario,expected_audio_wavs=args.expected_audio_wav)
        else:
            from upstreams import collect
            report=collect(args.repository,since=args.since,until=args.until)
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with report_path.open('x') as handle: json.dump(report,handle,indent=2)
        print(json.dumps(report,indent=2))
        if args.command=='qualify-port' and not report['structural_checks_passed']:
            raise RuntimeError('Port qualification found errors; inspect report')
        if args.command=='review-upstreams' and any(r['status']=='unavailable' for r in report['repositories']):
            raise RuntimeError('Some upstream sources unavailable; report preserves partial coverage')
        return
    if args.command == 'source-package':
        package_source()
        return
    if args.command == 'oracle-check':
        from recovery import qualify_oracle, implementation
        print(json.dumps(dict(qualification=qualify_oracle(), implementation=implementation()), indent=2))
        return
    if args.command == 'instruction-check':
        from conformance import run
        path = Path(args.report) if args.report else None
        if path and (path.exists() or path.is_symlink()):
            raise ValueError('Conformance report already exists')
        report = run(args.backend, args.allow_known_rounding)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x') as handle:
                json.dump(report, handle, indent=2)
                handle.write('\n')
        print(json.dumps(report, indent=2))
        if not report['accepted']:
            raise RuntimeError('Instruction check is not accepted; inspect deviations/failures')
        return
    if args.command == 'knowledge-add':
        from knowledge import add_rule
        print(json.dumps(add_rule(args.bank, args.candidate), indent=2))
        return
    if args.command == 'package-accepted-app':
        from release import package
        report = package(args.source, args.destination, args.title, args.version, args.bundle_id, args.icon, args.log,bundle_assets=args.bundle_assets)
        print(json.dumps(report, indent=2))
        return
    if args.command == 'next-game':
        from next_game import run, create_profile
        if args.profile is None:
            raise ValueError('next-game needs an explicit per-game --profile path (it is created when missing)')
        if not Path(args.profile).exists():
            if not args.title or not args.source:
                raise ValueError('New profile requires --title and --source')
            if not args.execute:
                raise ValueError('Create a profile with new-profile first, or use --execute with --title/--source')
            create_profile(args.profile, args.title, args.source)
        elif args.title:
            existing, _ = load_profile(args.profile)
            if existing.get('title') != args.title:
                raise ValueError(f'Profile {args.profile} belongs to {existing.get("title")!r}; use a separate profile for another title')
        report = run(args.profile, args.name, args.source, args.through, args.execute, args.resume, image=args.image)
        print(json.dumps({'status':report['status'], 'playable_certified':False,
                          'remaining_gates':report['remaining_gates']}, indent=2))
        if report['status'] == 'blocked': sys.exit(2)
        return
    if args.command == 'doctor':
        print(platform.platform())
        print('Python:', sys.version.split()[0])
        for name in ['git', 'clang++', 'sandbox-exec']:
            print(name + ':', shutil.which(name) or 'missing')
        print('Tool work:', WORK)
        print('PS2Recomp built:', (BUILD / 'ps2xRecomp/ps2_recomp').exists())
        return
    if args.command == 'bootstrap':
        bootstrap()
        return
    if args.command == 'bootstrap-runtime':
        from native import bootstrap_runtime
        bootstrap_runtime()
        return
    if args.command == 'new-profile':
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.id):
            raise ValueError('Invalid profile id')
        path = Path(args.output).resolve() if args.output else PRIVATE_ROOT / f'profiles/{args.id}.json'
        if path.stem != args.id or path.suffix != '.json':
            raise ValueError('Profile filename must be <id>.json')
        if path.exists():
            raise ValueError('Profile already exists')
        if args.source:
            from next_game import create_profile
            create_profile(path, args.title, args.source)
        else:
            save(path, {'id': args.id, 'title': args.title, 'runtime_status': 'not_integrated'})
        print(path)
        print(f'Use it explicitly: python3 run.py --profile "{path}" <command>')
        return
    if args.command in PROFILE_COMMANDS_WITHOUT_PROFILE:
        raise RuntimeError('Unhandled profile-free command: ' + args.command)
    profile, private = load_profile(args.profile)
    private.mkdir(parents=True, exist_ok=True)
    if args.command == 'rebase-generated-corrections':
        from generated_rebase import rebase
        from clean_build import path_in
        prior_profile, prior_private = load_profile(args.previous_profile)
        specification = prior_profile.get('generated_source_corrections')
        if not specification:
            raise ValueError('Previous profile declares no pinned title corrections')
        bases = json.loads(path_in(private, args.bases).read_text()) if args.bases else None
        report = rebase(profile, private, prior_private, specification, bases, args.destination)
        print(json.dumps(report, indent=2))
        if report['conflicts']:
            raise RuntimeError('Rebase has conflicts; candidate sources retained, no replay manifest activated')
    elif args.command == 'recover-function':
        from recovery import run
        report = run(profile, private, args.spec, args.name, args.bank, args.reuse, args.bindings)
        print(json.dumps({k:report[k] for k in ('status','cases','matched','accepted_on_cases','universal_equivalence','promoted_to_runtime')}, indent=2))
        if not report['accepted_on_cases']: sys.exit(2)
    elif args.command == 'find-recovery-candidates':
        from recovery import load_spec, matches
        code, _, _, _ = load_spec(profile, private, args.spec)
        print(json.dumps(dict(candidates=matches(private, code, args.bank), accepted_for_target=False), indent=2))
    elif args.command == 'collect-runtime-targets':
        from feedback import prepare
        report = prepare(profile, private, args.log, args.name)
        print(json.dumps(report, indent=2))
    elif args.command == 'freeze-source':
        from source_freeze import freeze
        report=freeze(profile,private,args.compile_db,args.unit,args.source,args.reference,args.name)
        print(json.dumps({'status':report['status'],'text_matches':report.get('text_matches'),
                          'probe_matches_reference':report.get('probe_matches_reference'),
                          'whole_game_clean_build':False},indent=2))
        if report['status']!='passed': sys.exit(2)
    elif args.command == 'code-census':
        from code_census import census
        census(private, profile)
    elif args.command == 'translate-overlays':
        from overlays import translate_overlays, check_overlays
        translate_overlays(profile, private)
        check_overlays(private)
    elif args.command == 'enhancement-plan':
        from enhancements import schedule
        report=schedule(profile,private)
        print(json.dumps({'qualified':report['completed'],'total':report['total'],'policy':report['policy']},indent=2))
    elif args.command == 'knowledge-plan':
        from knowledge import plan
        report = plan(profile, private)
        print(json.dumps(report, indent=2))
        if report.get('memory') and not report['memory']['accepted']: sys.exit(2)
    elif args.command == 'import-preserved-copy':
        from preserved_disc import import_copy
        print(json.dumps(import_copy(args.source,args.manifest,args.sha256,profile,private),indent=2))
    elif args.command == 'import':
        import_disc(args.source, profile, private)
    elif args.command == 'import-image':
        from cdimage import import_image
        import_image(args.source, private)
    elif args.command == 'scan-pointer-entries':
        from pointer_entries import apply
        apply(profile, private, write_config=not args.report_only)
    elif args.command == 'enhance-textures':
        from textures import enhance
        report = enhance(profile, private, args.manifest, args.name)
        print(json.dumps({'status': report['status'], 'textures': len(report['textures']),
                          'runtime_integration': report['runtime_integration'],
                          'report': str(private/'texture-runs'/args.name/'report.json')}))
    elif args.command == 'stage-adapters':
        from adapters import stage_adapters
        report=stage_adapters(profile,private,args.manifest,args.name)
        print(json.dumps({'status':report['status'],'adapters':len(report['adapters']),
                         'report':str(private/'adapter-runs'/args.name/'report.json')}))
    elif args.command == 'compiler-toolchain':
        from toolchain import record_host
        print(record_host(private, args.name))
    elif args.command in {'compiler-plan', 'compiler-stage'}:
        from compiler import run_recipe
        report = run_recipe(profile, private, args.recipe, args.name, stage=args.command=='compiler-stage')
        print(json.dumps({'status':report['status'], 'clean_build_ready':report['clean_build_ready'],
            'blockers':report['blockers'], 'report':str(private/'compiler-runs'/args.name/'report.json')}))
    elif args.command == 'match-function':
        from match_function import run as match_function
        report = match_function(profile, private, args.function, args.source, args.compiler, args.name,
                                tuple(args.flags or ('-O2', '-G0')))
        print(json.dumps({k: report[k] for k in ('function', 'compiler', 'flags', 'match', 'original_bytes',
                                                 'compiled_bytes', 'differing_words')}, indent=2))
    elif args.command == 'rebuild-binary':
        from binary_rebuild import run as rebuild_binary
        report = rebuild_binary(profile, private, args.name, args.gp)
        print(json.dumps({k: report[k] for k in ('identical', 'original_sha256', 'rebuilt_sha256', 'instructions',
                                                 'undecoded', 'labels', 'output')}, indent=2))
        if not report['identical']:
            raise SystemExit(1)
    elif args.command == 'memory-audit':
        from memory import run_audit
        report = run_audit(profile, private, args.runtime_header, args.layout, args.name)
        print(json.dumps({'status': report['status'], 'accepted': report['accepted'],
                          'report': str(private/'analysis/memory-audits'/args.name/'report.json')}))
        if not report['accepted']:
            sys.exit(2)
    elif args.command == 'replay-raw-gs':
        from replay import run_replay
        report = run_replay(profile, private, args.capture, args.backend, args.name, args.batch, args.timeout)
        print(json.dumps({'status': report['status'], 'matches_recorded_footer': report.get('matches_recorded_footer'),
                          'report': str(private / 'logs/replays' / args.name / 'report.json')}))
        if report['status'] != 'observed':
            sys.exit(2)
    elif args.command == 'analyze-cadence':
        from cadence import analyze_capture
        report = analyze_capture(profile, private, args.capture, args.name, args.expected_field_hz)
        print(json.dumps(report))
    elif args.command == 'analyze-vu-dependencies':
        from vu_dependencies import analyze_capture
        print(json.dumps(analyze_capture(private,args.capture,args.name),indent=2))
    elif args.command == 'compile-vu':
        from vectors import compile_vu
        compile_vu(profile,private,args.capture)
    elif args.command == 'scenario':
        from scenarios import run_scenario
        report = run_scenario(profile, private, args.definition, args.name, args.renderer_worker, args.graphics_history)
        if report['status'] != 'observed':
            sys.exit(2)
    elif args.command == 'compare-scenarios':
        from scenarios import compare_scenarios
        report=compare_scenarios(private, args.baseline, args.candidate, args.name)
        if not report['eligible_for_timing_comparison']:
            sys.exit(2)
    elif args.command == 'ab-check':
        from ab_check import run_ab_check
        report = run_ab_check(args.profile, args.name, args.baseline_lab, args.candidate_lab,
                              args.baseline_patches, args.candidate_patches, args.baseline_knowledge,
                              args.candidate_knowledge, args.scenario, args.boot_seconds,
                              args.reuse_generated, args.output, args.allow_busy, args.jobs)
        if report['comparison']['status'] != 'no_regression':
            sys.exit(2)
    elif args.command == 'audit-reference-overlays':
        from reference_functions import audit_port
        report=audit_port(profile,private)
        print(json.dumps(report,indent=2))
        if not report['passed']:raise RuntimeError('Reference function-entry coverage is incomplete; inspect local audit')
    elif args.command == 'translate':
        translate(profile, private, args.feedback)
    elif args.command == 'prepare':
        verify(profile, private)
        inspect(profile, private)
        analyze(profile, private)
        translate(profile, private)
    elif args.command == 'check-generated':
        check_generated(profile, private)
    elif args.command in {'build-native', 'test-runtime', 'boot-probe', 'package-local'}:
        import native
        if args.command == 'package-local' and args.output_directory is not None:
            native.package_local(profile, private, output_directory=args.output_directory)
        else:
            getattr(native, args.command.replace('-', '_'))(profile, private)
    elif args.command == 'diagnose':
        import native
        native.boot_probe(profile, private, headless=True)
    else:
        globals()[args.command](profile, private)


def cli(target=None):
    """Shared command-line wrapper for pipeline.py, run.py targets and platform adapters."""
    try:
        main(target)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)



SOURCE_FILES = ('runtime/gpu/native-plugin/scanout_qualification.hpp', 'tests/scanout_qualification.cpp', 'tests/test_scanout_qualification.py', 'patches/dbcman-v2-controller.patch', 'patches/vu0-concurrent.patch') + SOURCE_FILES

SOURCE_FILES = ('tests/test_mpeg_fragment_decode.py',) + SOURCE_FILES

SOURCE_FILES = ('patches/mpeg-multistream-continuity.patch',) + SOURCE_FILES

SOURCE_FILES = ('tests/test_correction_syntax.py', 'generated_rebase.py', 'tests/test_generated_rebase.py', 'tests/test_signing_space.py', 'resident_closure.py', 'tests/test_resident_closure.py', 'patches/vu-product-underflow.patch',) + SOURCE_FILES


SOURCE_FILES = ('patches/link-branch-resume.patch', 'tests/test_link_branch_resume.py') + SOURCE_FILES
SOURCE_FILES = ('patches/gs-load-image-host-packet.patch', 'tests/check_vu0_poll.py') + SOURCE_FILES
SOURCE_FILES = ('patches/gs-image-transfer-width.patch',) + SOURCE_FILES
SOURCE_FILES = ('patches/host-frame-resource-lifetime.patch',) + SOURCE_FILES
SOURCE_FILES = ('patches/libgraph-image-packet-abi.patch',) + SOURCE_FILES
SOURCE_FILES = ('patches/discovered-sdk-bindings.patch', 'tests/test_discovered_sdk_bindings.py') + SOURCE_FILES



SOURCE_FILES = ('patches/mutable-jump-table-entries.patch', 'tests/test_mutable_jump_table.py') + SOURCE_FILES

SOURCE_FILES = ('indirect_entries.py', 'tests/test_indirect_entries.py') + SOURCE_FILES

SOURCE_FILES = ('overlay_segments.py', 'tests/test_overlay_segments.py') + SOURCE_FILES

SOURCE_FILES = ('reference_functions.py', 'tests/test_reference_functions.py') + SOURCE_FILES

SOURCE_FILES = ('REFERENCE-QUALIFICATION.md',) + SOURCE_FILES

SOURCE_FILES = ('runtime/gpu/native-plugin/completion_gate.hpp', 'tests/completion_gate.cpp') + SOURCE_FILES

SOURCE_FILES = ('runtime/gpu/src/alpha_proof.h', 'tests/alpha_proof.cpp') + SOURCE_FILES

SOURCE_FILES = ('tests/test_alpha_proof.py', 'tests/alpha_gpu_compare.cpp') + SOURCE_FILES

SOURCE_FILES = ('runtime/gpu/src/ordered_coverage.h', 'tests/ordered_coverage.cpp', 'tests/test_module_layout.py') + SOURCE_FILES


if __name__ == '__main__':
    cli()
