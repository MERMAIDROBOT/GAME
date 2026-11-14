"""A controller streamed by Sunshine/Moonlight reaches the game as pad 1 without a virtual HID driver.

Sunshine on macOS creates virtual controllers only through a separately licensed driver (libvirtualhid's broker);
without one, Moonlight controller input was dropped on the host. A local Sunshine build sends each controller's
state as a 28-byte datagram to ~/Library/Application Support/PS2 Port Lab/stream-pad.sock, and the engine merges
controller 0 into its host pad with the keyboard and a local controller: Xbox A/B/X/Y -> Cross/Circle/Square/
Triangle, Back -> Select, triggers -> L2/R2, sticks onto the PS2's 0-255 axes (Y down). A press released between
two pad samples is latched like a quick key tap. The native check builds the receiver from the patch and sends it
real datagrams.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'patches/stream-pad.patch'

NATIVE_CHECK = r'''
#include "stream_pad_input.hpp"
#include <chrono>
#include <cstdio>
#include <thread>
using namespace StreamPad;
static const Ps2Bits bits{0x80,0x40,0x20,0x10,0x08,0x04,0x02,0x01,0x8000,0x4000,0x2000,0x1000,0x0800,0x0400,0x0200,0x0100};
static Packet pkt(uint32_t buttons, uint8_t active = 1, uint8_t lt = 0, int16_t lsx = 0, int16_t lsy = 0) {
    Packet p{}; p.magic = kMagic; p.version = 1; p.index = 0; p.active = active; p.buttons = buttons; p.lt = lt;
    p.lsX = lsx; p.lsY = lsy; return p;
}
int main() {
    Packet a = pkt(kA | kUp | kBack, 1, 200);
    if (buttonsFrom(a, bits) != (0x4000 | 0x10 | 0x01 | 0x0100)) return 1;          // Cross, Up, Select, L2
    if (axis(32767, false) != 255 || axis(-32768, false) != 0 || axis(0, false) != 128) return 2;
    if (axis(32767, true) != 0) return 3;                                              // stick up -> 0 on the PS2
    // Socket round trip, then a quick tap: pressed and released before the next sample is still reported once.
    auto &r = Receiver::instance();
    const char *path = std::getenv("PS2_PORT_STREAM_PAD_SOCKET");
    int fd = ::socket(AF_UNIX, SOCK_DGRAM, 0);
    sockaddr_un to{}; to.sun_family = AF_UNIX; std::strcpy(to.sun_path, path);
    auto send = [&](const Packet &p) { ::sendto(fd, &p, sizeof(p), 0, (const sockaddr *)&to, sizeof(to)); };
    send(pkt(kB)); send(pkt(0));
    for (int i = 0; i < 200 && !(r.take().active); ++i) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    // the tap above may already have been taken by the wait loop; repeat it now that the receiver is known live
    send(pkt(kB)); send(pkt(0));
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    Snapshot s = r.take();
    if (!s.active || s.buttons != 0 || !(s.pressedSince & kB)) { std::printf("tap %u %u\n", s.buttons, s.pressedSince); return 4; }
    if (r.take().pressedSince != 0) return 5;                                          // reported once
    Packet wrong = pkt(kA); wrong.magic = 1; send(wrong);                              // foreign datagrams ignored
    Packet second = pkt(kA); second.index = 1; send(second);                           // controller 1 is not pad 1
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    if (r.take().pressedSince != 0) return 6;
    send(pkt(0, 0));                                                                   // controller gone
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    if (r.take().active) return 7;
    std::puts("ok");
    return 0;
}
'''


def header_from_patch():
    """The new header exactly as the patch adds it."""
    text = (ROOT / PATCH).read_text()
    start = text.index('+++ b/ps2xRuntime/src/lib/stream_pad_input.hpp')
    body = text[start:].split('\n')[2:]
    return '\n'.join(line[1:] for line in body if line.startswith('+')) + '\n'


class Content(unittest.TestCase):
    def test_merge_into_host_pad(self):
        text = (ROOT / PATCH).read_text()
        for fragment in ('#include "stream_pad_input.hpp"', 'StreamPad::Receiver::instance().take()',
                         'freshPresses |= tapMask', 'clearBit(uint16_t(heldMask | tapMask))',
                         'PS2_PORT_STREAM_PAD', 'stream-pad.sock'):
            self.assertIn(fragment, text)

    def test_registered(self):
        self.assertIn(PATCH, native.RUNTIME_PATCHES)
        self.assertGreater(native.RUNTIME_PATCHES.index(PATCH), native.RUNTIME_PATCHES.index('patches/screenshot-key.patch'))
        for name in (PATCH, 'tests/test_stream_pad.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


class NativeReceiver(unittest.TestCase):
    def test_receiver(self):
        compiler = shutil.which('clang++')
        if not compiler:
            self.skipTest('clang++ not available')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'stream_pad_input.hpp').write_text(header_from_patch())
            source = Path(tmp) / 'check.cpp'
            source.write_text(NATIVE_CHECK)
            exe = Path(tmp) / 'check'
            subprocess.run([compiler, '-std=c++20', '-O1', f'-I{tmp}', str(source), '-o', str(exe)], check=True)
            env = dict(os.environ, PS2_PORT_STREAM_PAD_SOCKET=str(Path(tmp) / 'pad.sock'))
            result = subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
