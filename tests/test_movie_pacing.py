"""Movies shown as many small sent textures play at full speed, and the window shows the newest finished field.

SSX 3 shows each movie picture as ~120 strips 16 pixels wide, each sent by image transfer and then drawn. Three
things made its intro movies (EA BIG, THX, the snowboarding clips) play at 6-16 pictures a second:

1. Each strip was decoded on the GPU between draws, which ended and began the rendering pass around it (~80 passes
   and ~1600 barriers a frame, 40-48 ms of GPU time). Direct-colour textures the CPU holds are now decoded in the
   bridge (CpuDecode) and uploaded as a new image recorded ahead of the frame's commands (RecordAhead), so the pass
   goes on: GPU time fell to ~6 ms a frame.
2. The window took only the newest published field, whose capture was usually still running, so it kept the old
   picture: now it takes the newest field whose pixels are done (newestReady). Movie fields shown went from 15 to
   ~59.5 a second.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import pipeline

ROOT = Path(__file__).resolve().parents[1]
GPU = ROOT / 'runtime/gpu'

NATIVE_CHECK = r'''
#include "field_readiness.hpp"
#include <cstdio>
#include <deque>
struct Boundary { unsigned long long tick = 0; };
struct Field { Boundary boundary; bool ready = false; std::shared_future<int> pending; };
std::deque<std::promise<int>> promises;   // a dropped promise makes its future ready (broken_promise)
std::shared_ptr<const Field> make(unsigned long long tick, bool done) {
    auto f = std::make_shared<Field>();
    f->boundary.tick = tick;
    promises.emplace_back();
    f->pending = promises.back().get_future().share();
    if (done) promises.back().set_value(1);
    return f;
}
int main() {
    std::deque<std::shared_ptr<const Field>> recent{make(10, true), make(11, true), make(12, false)};
    auto pick = LocalGS::newestReady(recent, 0);
    if (!pick || pick->boundary.tick != 11) return 1;          // the newest done, not the newest
    if (LocalGS::newestReady(recent, 12)) return 2;            // nothing done at or after the floor
    recent.push_back(make(13, true));
    if (LocalGS::newestReady(recent, 0)->boundary.tick != 13) return 3;
    std::puts("ok");
    return 0;
}
'''


class Content(unittest.TestCase):
    def test_ahead_command_buffer_runs_first(self):
        text = (GPU / 'src/gpu_internal.h').read_text()
        self.assertIn('void RecordAhead(F&& record);', text)
        self.assertIn('VkCommandBuffer both[2] = {ahead_cmds[current_], cmd};', text)
        self.assertIn('submit.commandBufferCount = ahead ? 2 : 1;', text)
        gpu = (GPU / 'src/gpu.cpp').read_text()
        self.assertEqual(gpu.count('ahead_cmds'), 2)          # Init and InitShared

    def test_upload_is_a_new_image_ahead_of_the_pass(self):
        text = (GPU / 'src/gs_renderer.cpp').read_text()
        start = text.index('void VulkanGsRenderer::UploadTexture(')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('g_.RecordAhead(', body)
        self.assertNotIn('g_.Record(', body)
        self.assertIn('Retire(old);', body)                     # the old image outlives the draws that sample it
        self.assertIn('void VulkanGsRenderer::Retire(Texture texture)', text)

    def test_cpu_decode_only_direct_colour_single_level(self):
        text = (GPU / 'host-adapter/gpu_bridge.cpp').read_text()
        start = text.index('bool CpuDecodable(')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('levels == 1', body)
        self.assertIn('PS2_PORT_HW_GS_CPU_DECODE', body)
        for psm in ('PSMCT32', 'PSMCT24', 'PSMCT16', 'PSMCT16S'):
            self.assertIn('gs::' + psm, body)
        for palettized in ('PSMT8', 'PSMT4'):                  # they read the GPU's CLUT, in order with the draws
            self.assertNotIn('gs::' + palettized + ' ', body)
            self.assertNotIn('gs::' + palettized + ')', body)
        self.assertIn('b.renderer->UploadTexture(t.renderer_key, texels.data(), tw, th);', text)

    def test_publisher_picks_newest_ready(self):
        text = (GPU / 'native-plugin/field_publisher.hpp').read_text()
        self.assertIn('pick=newestReady(recent,floor);', text)
        self.assertIn('recent.clear();', text)

    def test_registered(self):
        self.assertIn('tests/test_movie_pacing.py', pipeline.SOURCE_FILES)


class NativeNewestReady(unittest.TestCase):
    def test_newest_ready(self):
        compiler = shutil.which('clang++')
        if not compiler:
            self.skipTest('clang++ not available')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'check.cpp'
            source.write_text(NATIVE_CHECK)
            exe = Path(tmp) / 'check'
            subprocess.run([compiler, '-std=c++20', '-O1', f'-I{GPU / "native-plugin"}', str(source), '-o', str(exe)],
                           check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
