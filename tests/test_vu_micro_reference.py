"""VU microprogram fixes (patches/vu-micro-reference.patch, tools/vu1_recomp.cpp VU0 images): patch
contents, VU0 capture handling, and, when the patched tool source is present, the interpreter and
compiled-code checks against the model and the PS2 hardware results."""
from pathlib import Path
import os
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / 'patches/vu-micro-reference.patch').read_text()


def added(path):
    section = PATCH.split('+++ b/' + path, 1)[1].split('\ndiff --git', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+') and not line.startswith('+++'))


def after(path):
    """The patched text of the hunks for `path` (added and unchanged lines)."""
    section = PATCH.split('+++ b/' + path, 1)[1].split('\ndiff --git', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines()
                     if line[:1] in ('+', ' ') and not line.startswith('+++'))


def removed(path):
    section = PATCH.split('+++ b/' + path, 1)[1].split('\ndiff --git', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('-') and not line.startswith('---'))


class PatchContent(unittest.TestCase):
    def test_efu_opcodes_follow_the_hardware_encoding(self):
        impl = after('ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        for code, name in (('0x78', 'ESQRT'), ('0x79', 'ERSQRT'), ('0x7A', 'ERCPR'), ('0x7C', 'ESIN'), ('0x7D', 'EATAN'),
                           ('0x7E', 'EEXP')):
            self.assertIn(f'case {code}: // {name}', impl)
        self.assertIn('case 0x77: // ERSQRT', removed('ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl'))
        core = after('ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp')
        self.assertIn('(0x77 is not an instruction)', core)
        self.assertIn('        case 0x7E:\n            usage.latency = 44u;', core)
        self.assertIn('        case 0x79:\n            usage.latency = 18u;', core)

    def test_fmac_uses_the_shared_lane_arithmetic_and_final_result_flags(self):
        impl = added('ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        self.assertIn('vu_reference::fmacLane(m_currentUpperInstruction, m_state, component, lane)', impl)
        self.assertIn('(void)dest;\n    return 0u;', impl)  # historical patch; superseded below
        followup = (ROOT / 'patches/vu-product-underflow.patch').read_text()
        self.assertIn('lane.productUnderflow', followup)
        self.assertIn('vu1.state().status, 0x100u', followup)
        gone = removed('ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        self.assertIn('vfmaq_f32', gone)  # compiled multiply-add is no longer fused
        self.assertIn('_mm_set1_epi32(0x7F7FFFFF)', gone)  # exponent 255 is no longer clamped
        self.assertIn('safe = _mm_and_ps(safe, _mm_and_ps(ordinary(s), ordinary(t)));', impl)

    def test_vu0_microprograms_have_a_compiled_path(self):
        core = added('ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp')
        self.assertIn('lookupCompiledVu0Program(vuCode, codeSize, memory)', core)
        self.assertIn('void registerVu0CompiledProgram', core)
        self.assertIn('"vu0_%016llx.bin"', core)
        impl = added('ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        self.assertIn('args.dataSize == 0x1000u && args.codeSize == 0x1000u', impl)
        self.assertIn('static VU1_FORCE_INLINE uint32_t qaddr0(', impl)
        compiler = (ROOT / 'tools/vu1_recomp.cpp').read_text()
        self.assertIn('registerVu0CompiledProgram(', compiler)
        self.assertIn('name.rfind("vu0_", 0) == 0', compiler)
        self.assertIn('vmaxBits(', compiler)
        self.assertNotIn('_mm_max_ps(', compiler)
        import native
        self.assertIn('patches/vu-micro-reference.patch', native.RUNTIME_PATCHES)

    def test_runtime_carries_the_exact_float_reference(self):
        self.assertEqual(added('ps2xRuntime/include/ps2float.h') + '\n', (ROOT / 'tools/reference/ps2float.h').read_text())
        self.assertIn('src/lib/vu/ps2float.cpp', added('ps2xRuntime/CMakeLists.txt'))


class Vu0Capture(unittest.TestCase):
    def test_capture_accepts_vu0_programs_and_checks_their_size(self):
        import vu_dependencies as deps
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            capture = private / 'capture'
            capture.mkdir()
            (capture / 'vu1_a.bin').write_bytes(bytes(16384))
            (capture / 'vu0_b.bin').write_bytes(bytes(4096))
            self.assertEqual([p.name for p in deps.capture_images(private, capture)], ['vu1_a.bin', 'vu0_b.bin'])
            self.assertEqual(deps.analyze_named('vu0_b.bin', bytes(4096))['status'], 'not_analyzed')
            (capture / 'vu0_c.bin').write_bytes(bytes(16384))
            with self.assertRaises(ValueError):
                deps.capture_images(private, capture)


def _patched_source():
    try:
        import pipeline as lab
    except Exception:
        return None
    inl = lab.SOURCE / 'ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl'
    if not inl.is_file() or 'vu_reference::fmacLane' not in inl.read_text(errors='replace'):
        return None
    return lab


@unittest.skipUnless(_patched_source() and (shutil.which('clang++') or shutil.which('g++')),
                     'patched tool source and a C++ compiler required')
class Behaviour(unittest.TestCase):
    def test_interpreter_and_compiled_programs_match_the_model(self):
        import vu_micro_check as vm
        forms = ['vadd', 'vmaddx', 'vmsuba', 'vopmsub', 'vdiv', 'vrsqrt', 'vclipw', 'vftoi4', 'vmax', 'lq', 'sq',
                 'vlqi', 'iaddiu', 'fmand', 'fseq', 'rnext', 'ersqrt', 'esadd', 'ibne', 'bal']
        for compiled in (False, True):
            report = vm.check(states=6, seed=3, only=forms, compiled=compiled)
            self.assertTrue(report['passed'], (compiled, report['failing_forms']))

    def test_hardware_results(self):
        import vu_hardware_check as hw
        report = hw.check()
        self.assertTrue(report['passed'], report['failures'][:3])
        self.assertGreaterEqual(report['exact'], 210)


if __name__ == '__main__':
    unittest.main()
