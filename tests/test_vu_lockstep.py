"""VU timing: programs run to their end (patches/vu-run-to-end.patch), and the compiled C++ VU programs
keep the interpreter's cycle timing, XGKICK transfers and results (vu_lockstep_check.py, which also
covers the MAX/MINI raw-operand fix in tools/vu1_recomp.cpp)."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / 'patches/vu-run-to-end.patch').read_text()


def added(path):
    section = PATCH.split('+++ b/' + path, 1)[1].split('\ndiff --git', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+') and not line.startswith('+++'))


class PatchContent(unittest.TestCase):
    def test_runtime_continues_vu_programs_to_their_end(self):
        self.assertIn('bool running() const { return m_running; }', added('ps2xRuntime/include/runtime/ps2_vu1.h'))
        self.assertIn('vu.m_running = !programEnded && !vu.m_stopRequested', added('ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp'))
        runtime = added('ps2xRuntime/src/lib/ps2_runtime.cpp')
        self.assertIn('void finishVuProgram(', runtime)
        # MSCAL and MSCNT (VU1) and VCALLMS (VU0) all continue the program.
        self.assertEqual(runtime.count('finishVuProgram(m_vu1,'), 2)
        self.assertEqual(runtime.count('finishVuProgram(m_vu0,'), 1)

    def test_compiled_max_and_mini_read_raw_operands(self):
        compiler = (ROOT / 'tools/vu1_recomp.cpp').read_text()
        for form in ('"vmaxBits(" + rawV(fs) + ", " + rawBc(ft, bc)', '"vminBits(" + rawV(fs) + ", " + rawV(ft)',
                     '"vmaxBits(" + rawV(fs) + ", _mm_set1_ps(vu.m_state.i))", fd, true'):
            self.assertIn(form, compiler)
        self.assertNotIn('"vmaxBits(" + s + ', compiler)
        # Compiled VF0 writes are dropped in both views, and VI branch backups chain like the interpreter's.
        self.assertIn('// VF0 is constant: a result aimed at it only sets flags, in either view.', compiler)
        self.assertIn('vu.m_viBranchBackupValid && vu.m_viBranchBackupReg == ', compiler)
        self.assertNotIn('"vminBits(" + s + ', compiler)

    def test_vf0_is_restored_before_the_lower_instruction(self):
        impl = (ROOT / 'patches/vu-vf0-constant.patch').read_text()
        self.assertIn('+                // VF0 is constant: an upper result aimed at it', impl)

    def test_registered_and_packaged(self):
        for name in ('patches/vu-run-to-end.patch', 'patches/vu-vf0-constant.patch'):
            self.assertIn(name, native.RUNTIME_PATCHES)
        for name in ('patches/vu-run-to-end.patch', 'patches/vu-vf0-constant.patch', 'tests/vu_run_to_end.cpp',
                     'tests/vu_vf0_constant.cpp', 'tests/fixtures/vu-lockstep-regressions.json', 'vu_lockstep_check.py', 'tests/test_vu_lockstep.py'):
            self.assertIn(name, pipeline.SOURCE_FILES)


def _patched_source():
    header = pipeline.SOURCE / 'ps2xRuntime/include/runtime/ps2_vu1.h'
    return header.is_file() and 'bool running() const' in header.read_text(errors='replace')


@unittest.skipUnless(_patched_source() and shutil.which('clang++'), 'patched runtime source and clang++ required')
class Behaviour(unittest.TestCase):
    def _run_program(self, source):
        import vu_micro_check as vm
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            flags, runtime = vm._flags(work)
            built = subprocess.run(flags + [str(ROOT / source), str(runtime / 'src/lib/vu/ps2_vu1_core.cpp'),
                                            str(runtime / 'src/lib/vu/ps2float.cpp'), '-Wl,-undefined,dynamic_lookup',
                                            '-o', str(work / 'program')], capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            ran = subprocess.run([str(work / 'program')], capture_output=True, text=True, timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stdout)

    def test_long_program_runs_to_its_end_in_slices(self):
        self._run_program('tests/vu_run_to_end.cpp')

    def test_vf0_stays_constant_within_a_pair(self):
        self._run_program('tests/vu_vf0_constant.cpp')

    def test_compiled_programs_keep_interpreter_timing(self):
        import vu_lockstep_check as lockstep
        report = lockstep.check(programs=16, length=120, seed=5)
        self.assertTrue(report['passed'], report['details'][:5])

    def test_shrunk_regressions(self):
        import vu_lockstep_check as lockstep
        with tempfile.TemporaryDirectory() as tmp:
            results, details, _ = lockstep.run(lockstep.regression_cases(), Path(tmp))
            self.assertTrue(all(r == [True, True] for r in results), details[:5])

    def test_max_and_mini_on_special_operands(self):
        import vu_lockstep_check as lockstep
        with tempfile.TemporaryDirectory() as tmp:
            results, details, _ = lockstep.run(lockstep.operand_cases(), Path(tmp))
            self.assertTrue(all(r == [True, True] for r in results), details[:5])

    def test_stores_racing_an_xgkick_transfer(self):
        import vu_lockstep_check as lockstep
        with tempfile.TemporaryDirectory() as tmp:
            results, details, _ = lockstep.run(lockstep.xgkick_cases(), Path(tmp))
            self.assertTrue(all(r == [True, True] for r in results), details[:5])
        # Negative control: compiled stores that skip the PATH1 catch-up must be caught.
        with tempfile.TemporaryDirectory() as tmp:
            results, details, _ = lockstep.run(lockstep.xgkick_cases(), Path(tmp), mutate=(
                'ps2_vu1_impl.inl', '        xgkickSync(vu, ctx, cycle);\n        storeDest(', '        storeDest('))
            self.assertTrue(any(r != [True, True] for r in results))
            self.assertTrue(any('packets' in d for d in details))

if __name__ == '__main__':
    unittest.main()
