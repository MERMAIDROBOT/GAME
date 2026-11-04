"""Display path: interlaced field-mode weave and bilinear window scaling (presentation-fidelity), the GPU renderer
host (plugin loader, packed HD upload, vsync pacing), window-thread failure reporting, and host-frame strides."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ('presentation-fidelity', 'gpu-renderer-host', 'window-thread-failure', 'hd-frame-stride', 'vsync-setting')


def text(name):
    return (ROOT / f'patches/{name}.patch').read_text()


class Content(unittest.TestCase):
    def test_registered_in_order_and_packaged(self):
        order = [native.RUNTIME_PATCHES.index(f'patches/{name}.patch') for name in PATCHES]
        self.assertEqual(order, sorted(order))
        for name in PATCHES:
            self.assertIn(f'patches/{name}.patch', pipeline.SOURCE_FILES)
        for name in ('tests/display_path.cpp', 'tests/test_display_path.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)

    def test_field_mode_weaves_and_window_filters(self):
        patch = text('presentation-fidelity')
        self.assertIn('return; // Field mode: the buffer already holds both fields', patch)
        self.assertIn('SetTextureFilter(frameTex, TEXTURE_FILTER_BILINEAR)', patch)
        self.assertIn('PS2_PORT_PRESENT_NEAREST', patch)

    def test_renderer_host(self):
        patch = text('gpu-renderer-host')
        for fragment in ('dlsym(plugin, "PS2PortCreateRawGSBackend")', 'backend->OwnsRawGS()', 'm_gs.setRasterBackend',
                         'FLAG_VSYNC_HINT', 'PresentationPacing::swapped();', 'PS2_PORT_ENHANCE_HIRES_SCANOUT'):
            self.assertIn(fragment, patch)
        self.assertNotIn('0x1a1468', patch)          # no title debugging hooks in the shared runtime
        self.assertIn('PS2_PORT_ENHANCE_VSYNC', text('vsync-setting'))

    def test_window_failure_joins_before_rethrow(self):
        patch = text('window-thread-failure')
        self.assertIn('[run] window thread stopped: ', patch)
        self.assertIn('std::rethrow_exception(windowFailure);', patch)

    def test_frame_copy_uses_real_stride(self):
        patch = text('hd-frame-stride')
        self.assertIn('gsHostFrameStride(outWidth, outHeight, m_hostPresentationFrame.size())', patch)
        self.assertIn('-        const size_t sourceRowBytes = static_cast<size_t>(kHostFrameWidth) * 4u;', patch)


def _patched():
    return (pipeline.SOURCE / 'ps2xRuntime/include/runtime/gs/gs_frame_layout.h').is_file()


@unittest.skipUnless(_patched() and shutil.which('clang++'), 'patched runtime source and clang++ required')
class Behaviour(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'display_path'
            subprocess.run(['clang++', '-std=c++17', '-I', str(pipeline.SOURCE / 'ps2xRuntime/include'),
                            str(ROOT / 'tests/display_path.cpp'), '-o', str(exe)], check=True)
            ran = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == '__main__':
    unittest.main()
