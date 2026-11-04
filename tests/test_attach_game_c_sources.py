"""The runner's C++ precompiled headers and unity batches must not reach its C environment cache
(cmake/AttachGame.cmake). Without the exclusion the PCH is also compiled as C and fails on <cstdint>."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import pipeline as lab

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / 'cmake/AttachGame.cmake').read_text()
CALL = re.search(r'set_source_files_properties\("\$\{port_environment_source\}".*?\)\n', TEXT, re.S)


def tool(name):
    local = lab.ENV / 'bin' / name
    return str(local) if local.exists() else shutil.which(name)


def cmake():
    return tool('cmake')


class Content(unittest.TestCase):
    def test_c_cache_skips_pch_and_unity(self):
        self.assertIsNotNone(CALL)
        self.assertIn('SKIP_PRECOMPILE_HEADERS ON', CALL[0])
        self.assertIn('SKIP_UNITY_BUILD_INCLUSION ON', CALL[0])


@unittest.skipUnless(cmake() and tool('ninja') and shutil.which('c++'), 'cmake, ninja and a compiler required')
class Behaviour(unittest.TestCase):
    def _build(self, exclusion):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src'; src.mkdir()
            (src / 'pch.h').write_text('#include <cstdint>\n')
            (src / 'main.cpp').write_text('extern "C" int cache_value(void);\nint main() { return cache_value(); }\n')
            (src / 'cache.c').write_text('int cache_value(void) { return 0; }\n')
            (src / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.20)\nproject(T LANGUAGES CXX)\n'
                'add_executable(ps2EntryRunner main.cpp)\n'
                'set_target_properties(ps2EntryRunner PROPERTIES UNITY_BUILD ON)\n'
                'target_precompile_headers(ps2EntryRunner PRIVATE pch.h)\n'
                'enable_language(C)\nset(port_environment_source "${CMAKE_CURRENT_SOURCE_DIR}/cache.c")\n'
                'target_sources(ps2EntryRunner PRIVATE "${port_environment_source}")\n' + (CALL[0] if exclusion else ''))
            build = Path(tmp) / 'build'
            subprocess.run([cmake(), '-S', str(src), '-B', str(build), '-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + tool('ninja')], check=True, capture_output=True)
            return subprocess.run([cmake(), '--build', str(build)], capture_output=True, text=True)

    def test_runner_with_c_cache_builds(self):
        result = self._build(True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])

    def test_negative_control_without_exclusion_fails(self):
        self.assertNotEqual(self._build(False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
