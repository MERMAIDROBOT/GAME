"""Synthetic runtime flag policy and packaging checks; runtime differential suites are separate."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
import pipeline,native
class HotpathTests(unittest.TestCase):
    def test_flags_cached_and_dynamic_optout(self):
        patch=(pipeline.ROOT/'patches/runtime-hotpaths.patch').read_text()
        fragment=patch.split('+++ b/ps2xRuntime/include/ps2_env_flags.h\n',1)[1]
        header=''.join(l[1:] for l in fragment.splitlines(True) if l.startswith('+'))
        compiler=shutil.which('clang++') or shutil.which('g++');self.assertTrue(compiler)
        src='''#include "ps2_env_flags.h"
#include <cstdlib>
int main(){unsetenv("PS2_PORT_VU_TRACE");if(ps2_port_env::vuTrace())return 1;
setenv("PS2_PORT_VU_TRACE","1",1); bool dynamic=!ps2_port_env::cacheEnabled();
if(ps2_port_env::vuTrace()!=dynamic)return 2;
if(ps2_port_env::vifFast()!=ps2_port_env::enabled("PS2_PORT_VIF_FASTPATH"))return 3;
if(ps2_port_env::lazyTimers()!=ps2_port_env::enabled("PS2_PORT_LAZY_EE_TIMERS"))return 4;return 0;}'''
        with tempfile.TemporaryDirectory() as tmp:
            h=Path(tmp);(h/'ps2_env_flags.h').write_text(header);(h/'test.cpp').write_text(src)
            subprocess.run([compiler,'-std=c++20',str(h/'test.cpp'),'-o',str(h/'test')],check=True,capture_output=True)
            for cache in ['0','1']:
                for mode in ['0','1']:
                    env={**os.environ,'PS2_PORT_ENV_CACHE':cache,'PS2_PORT_LAZY_EE_TIMERS':mode,'PS2_PORT_VIF_FASTPATH':mode}
                    self.assertEqual(subprocess.run([str(h/'test')],env=env).returncode,0)
    def test_public_patch_has_no_private_window(self):
        p=(pipeline.ROOT/'patches/runtime-hotpaths.patch').read_text()
        self.assertNotIn('claude_asset_window',p);self.assertNotIn('/Users/',p)
        self.assertIn('m_lazyEeTimers',p);self.assertNotIn('static ClaudeLazyTimers',p)
        for name in ['patches/runtime-hotpaths.patch','patches/scalar-fpu-compare.patch']:
            self.assertIn(name,native.RUNTIME_PATCHES);self.assertIn(name,pipeline.SOURCE_FILES)
