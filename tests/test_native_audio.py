"""Compile the carried PCM backend itself; no audio device or game assets."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('clang++') or shutil.which('g++'),'C++ compiler required')
class NativeAudio(unittest.TestCase):
    def test_actual_pcm_ring_drift_channels_wrap_and_reprime(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'runtime').mkdir()
            shutil.copy2(ROOT/'runtime/native_audio/ps2_audio.h',p/'runtime/ps2_audio.h')
            (p/'runtime/ps2_memory.h').write_text('#pragma once\nconstexpr unsigned PS2_RAM_SIZE=0x02000000u;\nconstexpr unsigned PS2_RAM_MASK=PS2_RAM_SIZE-1;\n')
            compiler=shutil.which('clang++') or shutil.which('g++')
            cmd=[compiler,'-std=c++20','-O2','-pthread','-I'+str(p),
                 '-I'+str(ROOT/'tests/reference/raylib'),str(ROOT/'runtime/native_audio/ps2_audio.cpp'),
                 str(ROOT/'tests/native_audio_drift.cpp'),'-o',str(p/'probe')]
            built=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT_','PS2X_'))}
            # Native PCM must work by default, without an opt-in environment flag.
            run=subprocess.run([str(p/'probe')],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_actual_host_input_backend_matches_shared_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'runtime').mkdir()
            (p/'runtime/ps2_pad.h').write_text('''#pragma once
#include <cstdint>
#include <cstddef>
class PSPadBackend {public:bool readState(int,int,uint8_t*,size_t);};
''')
            compiler=shutil.which('clang++') or shutil.which('g++')
            cmd=[compiler,'-std=c++20','-O2','-pthread','-I'+str(p),
                 '-I'+str(ROOT/'tests/reference/raylib'),str(ROOT/'runtime/host_input/ps2_pad.cpp'),
                 str(ROOT/'tests/host_pad.cpp'),'-o',str(p/'pad')]
            built=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            run=subprocess.run([str(p/'pad')],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
