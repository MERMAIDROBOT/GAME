"""GS host rounding (patches/gs-host-rounding.patch): a VU program runs under round-toward-zero and
sends PATH1 packets through XGKICK while it is still running. The same GS packet must draw the same
pixels whatever rounding mode its caller left active, and the caller's mode must survive the call."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / 'patches/gs-host-rounding.patch').read_text()
GUARD = '    const GSHostRounding rounding;\n'
ENTRY_POINTS = ('void GS::processGIFPacket(', 'bool GS::processNativePackedGIFPacket(', 'void GS::uploadImageNative(',
                'void GS::writeRegister(', 'bool GS::clearFramebufferContext(', 'bool GS::clearActiveFramebuffer(')


class PatchContent(unittest.TestCase):
    def test_every_public_drawing_entry_point_is_guarded(self):
        self.assertIn('+++ b/ps2xRuntime/include/runtime/gs/gs_host_rounding.h', PATCH)
        self.assertIn('std::fesetround(FE_TONEAREST)', PATCH)
        self.assertEqual(PATCH.count('+' + GUARD), len(ENTRY_POINTS))

    def test_patch_is_registered_and_packaged(self):
        self.assertIn('patches/gs-host-rounding.patch', native.RUNTIME_PATCHES)
        for name in ('patches/gs-host-rounding.patch', 'tests/gs_host_rounding.cpp', 'tests/test_gs_host_rounding.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


def _patched_runtime():
    frontend = pipeline.SOURCE / 'ps2xRuntime/src/lib/gs/gs_frontend.cpp'
    if not frontend.is_file() or 'GSHostRounding' not in frontend.read_text(errors='replace'):
        return None
    return pipeline.SOURCE / 'ps2xRuntime'


@unittest.skipUnless(_patched_runtime() and shutil.which('clang++'), 'patched runtime source and clang++ required')
class Behaviour(unittest.TestCase):
    def _build(self, work, frontend, name):
        runtime = _patched_runtime()
        flags = [part for part in pipeline.syntax_check_command(work) if part not in ('-fsyntax-only', '-ferror-limit=8')]
        sources = [frontend] + [runtime / 'src/lib/gs' / s for s in ('gs_cpu_backend.cpp', 'gs_threaded_backend.cpp', 'ps2_gs_memory.cpp')]
        out = work / name
        built = subprocess.run(flags + ['-O2', '-w', str(ROOT / 'tests/gs_host_rounding.cpp'), *map(str, sources), '-o', str(out)],
                               capture_output=True, text=True, timeout=300)
        self.assertEqual(built.returncode, 0, built.stderr[-2000:])
        return out

    def test_pixels_do_not_depend_on_the_callers_rounding_mode(self):
        runtime = _patched_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            fixed = self._build(work, runtime / 'src/lib/gs/gs_frontend.cpp', 'fixed')
            # Negative control: the same frontend without the guard must show the defect.
            unguarded = work / 'gs_frontend_unguarded.cpp'
            unguarded.write_text((runtime / 'src/lib/gs/gs_frontend.cpp').read_text().replace(GUARD, ''))
            control = self._build(work, unguarded, 'control')
            env = {k: v for k, v in os.environ.items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
            for thread in ('0', '1'):  # inline and threaded renderers
                run = dict(env, PS2_PORT_GS_THREAD=thread)
                ok = subprocess.run([str(fixed)], capture_output=True, text=True, env=run, timeout=60)
                self.assertEqual(ok.returncode, 0, (thread, ok.stdout))
                bad = subprocess.run([str(control)], capture_output=True, text=True, env=run, timeout=60)
                self.assertEqual(bad.returncode, 1, (thread, bad.stdout))


if __name__ == '__main__':
    unittest.main()
