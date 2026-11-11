"""PS2_PORT_ENHANCE_DISPLAY_LOCK=1 ties the emulated vertical blank and the window to the display's own refresh.

The emulated field clock ran on a free 16.667 ms timer and the window swapped with vsync on, so two 60 Hz clocks
beat against each other: in an SSX 3 race the window swapped about 104 times a second (1-3 ms and 15-17 ms apart)
and a quarter of the game's pictures reached the panel one refresh early or late. With the lock the window waits
for the display link and presents once per refresh (60.0 swaps/s), and each field is due half a refresh after a
display refresh: 93% of pictures land on their refresh. The native check compiles the patched header and steps
the phase lock from a bad phase."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import launch_config
import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/display-lock.patch'

NATIVE_CHECK = r'''
#include "runtime/presentation_pacing.hpp"
#include <cstdio>
int main() {
    auto &clock = PresentationPacing::displayClock();
    const int64_t p = 16'665'000, now = PresentationPacing::steadyNs();
    clock.started = true; clock.periodNs = p; clock.lastNs = now;
    // A field due 1 ms after a refresh is pulled toward the half-refresh phase, at most 0.5 ms per field.
    int64_t due = now + 1'000'000;
    for (int i = 0; i < 80; ++i) {
        const int64_t next = PresentationPacing::lockedFieldAfter(due, 16'667'000);
        const int64_t step = next - due;
        if (step < p - 500'000 || step > p + 500'000) { std::printf("step %lld\n", (long long)step); return 1; }
        due = next;
    }
    int64_t phase = (due - now) % p;
    if (phase < p / 2 - 200'000 || phase > p / 2 + 200'000) { std::printf("phase %lld\n", (long long)phase); return 2; }
    // A 120 Hz panel locks two refreshes per field; a 75 Hz panel is not locked.
    clock.periodNs = 8'333'000;
    const int64_t two = PresentationPacing::lockedFieldAfter(due, 16'667'000) - due;
    if (two < 16'666'000 - 600'000 || two > 16'666'000 + 600'000) return 3;
    clock.periodNs = 13'333'000;
    if (PresentationPacing::lockedFieldAfter(due, 16'667'000) != 0) return 4;
    // A stale display clock (no refresh for 200 ms) is not followed.
    clock.periodNs = p; clock.lastNs = now - 300'000'000;
    if (PresentationPacing::lockedFieldAfter(due, 16'667'000) != 0) return 5;
    std::puts("ok");
    return 0;
}
'''


class Content(unittest.TestCase):
    def test_patch(self):
        text = (ROOT / PATCH).read_text()
        for fragment in ('PS2_PORT_ENHANCE_DISPLAY_LOCK', 'CVDisplayLinkCreateWithActiveCGDisplays',
                         'PresentationPacing::lockedFieldAfter(previousNs',
                         'PresentationPacing::waitDisplayRefresh();',
                         'PresentationPacing::vsync() && !displayLocked ? FLAG_VSYNC_HINT : 0'):
            self.assertIn(fragment, text)
        self.assertIn('return vsync() && v && v[0]', text)   # only together with the vsync setting

    def test_registered_and_declared(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        self.assertGreater(native.RUNTIME_PATCHES.index(PATCH), native.RUNTIME_PATCHES.index('patches/vsync-setting.patch'))
        for name in (PATCH, 'tests/test_display_lock.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)
        self.assertIn('PS2_PORT_ENHANCE_DISPLAY_LOCK', launch_config.BOOLS)


class NativePhaseLock(unittest.TestCase):
    def test_phase_lock(self):
        work = os.environ.get('PS2_PORT_LAB_WORK')
        header = Path(work or '/nonexistent') / 'dependencies/PS2Recomp/ps2xRuntime/include/runtime/presentation_pacing.hpp'
        compiler = shutil.which('clang++')
        if not header.exists() or not compiler or 'lockedFieldAfter' not in header.read_text():
            self.skipTest('patched runtime checkout or clang++ not available')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'check.cpp'
            source.write_text(NATIVE_CHECK)
            exe = Path(tmp) / 'check'
            subprocess.run([compiler, '-std=c++20', '-O1', f'-I{header.parents[1]}', str(source), '-o', str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
