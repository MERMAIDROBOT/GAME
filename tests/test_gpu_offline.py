"""The GPU renderer builds from this repository alone: vendored Vulkan headers, a generated loader with the volk API
subset, and committed SPIR-V that must match its GLSL. build-native builds the plugin when a profile declares
native_graphics, with MoltenVK as a pinned platform SDK."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
GPU = ROOT / 'runtime/gpu'


def tool(name):
    local = pipeline.ENV / 'bin' / name
    return str(local) if local.exists() else shutil.which(name)


class Content(unittest.TestCase):
    def test_fetching_is_opt_in(self):
        cmake = (GPU / 'CMakeLists.txt').read_text()
        self.assertIn('option(PS2GPU_FETCH_DEPENDENCIES "Fetch Vulkan-Headers, volk and glslang at configure time" OFF)', cmake)
        self.assertIn('set(PS2GPU_SHADER_DIR ${CMAKE_CURRENT_LIST_DIR}/shaders/spirv)', cmake)

    def test_vendored_headers_match_their_record(self):
        record = json.loads((GPU / 'third_party/vulkan-headers/ORIGIN.json').read_text())
        root = GPU / 'third_party/vulkan-headers'
        self.assertTrue(record['files'])
        for name, digest in record['files'].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
        self.assertIn('Apache-2.0', record['license'])

    def test_generated_files_are_current(self):
        for script in ('tools/gen_vk_loader.py', 'tools/gen_shaders.py'):
            ran = subprocess.run([sys.executable, str(ROOT / script), '--check'], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_stale_shader_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'repo'
            for part in ('runtime/gpu/shaders', 'runtime/gpu/third_party/smaa'):
                shutil.copytree(ROOT / part, copy / part)
            (copy / 'tools').mkdir()
            shutil.copy2(ROOT / 'tools/gen_shaders.py', copy / 'tools/gen_shaders.py')
            shader = copy / 'runtime/gpu/shaders/gs.frag'
            shader.write_text(shader.read_text() + '\n// changed\n')
            ran = subprocess.run([sys.executable, str(copy / 'tools/gen_shaders.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn('runtime/gpu/shaders/gs.frag changed', ran.stderr)

    def test_build_native_declares_graphics(self):
        source = (ROOT / 'native.py').read_text()
        self.assertIn("frameworks = build_native_graphics(profile, private)", source)
        self.assertIn("'-DFETCHCONTENT_FULLY_DISCONNECTED=ON'", source)
        self.assertEqual(native.build_native_graphics({}, Path('/nonexistent')), {})
        with self.assertRaises(ValueError):
            native.build_native_graphics({'native_graphics': {'unknown': 1}}, Path('/nonexistent'))


@unittest.skipUnless(tool('cmake') and tool('ninja') and (pipeline.SOURCE / 'ps2xRuntime/src/lib/gs/ps2_gs_memory.cpp').is_file(),
                     'cmake, ninja and the runtime source required')
class Behaviour(unittest.TestCase):
    def test_plugin_builds_disconnected(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure = subprocess.run([tool('cmake'), '-S', str(GPU / 'native-plugin'), '-B', tmp, '-G', 'Ninja',
                                        '-DCMAKE_MAKE_PROGRAM=' + tool('ninja'), '-DCMAKE_BUILD_TYPE=Release',
                                        '-DPS2_RUNTIME_SOURCE=' + str(pipeline.SOURCE / 'ps2xRuntime'),
                                        '-DFETCHCONTENT_FULLY_DISCONNECTED=ON'], capture_output=True, text=True)
            self.assertEqual(configure.returncode, 0, configure.stdout[-2000:] + configure.stderr[-2000:])
            self.assertNotIn('Populating', configure.stdout)
            built = subprocess.run([tool('cmake'), '--build', tmp, '--target', 'ps2_native_plugin'], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout[-3000:])
            plugin = Path(tmp) / native.GRAPHICS_PLUGIN
            exported = subprocess.run(['nm', '-gU', str(plugin)], capture_output=True, text=True).stdout
            self.assertIn('_PS2PortCreateRawGSBackend', exported)
            from native_components import binary_dependencies
            binary_dependencies(plugin)


if __name__ == '__main__':
    unittest.main()
