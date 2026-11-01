"""Asset-free checks for the shared runtime title-conformance patch (second-title lessons)."""
from pathlib import Path
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline as lab
import native

NAME = 'patches/runtime-title-conformance.patch'
PATCH = ROOT / NAME


def added(text, path):
    """Added lines of one file's hunks."""
    section = text.split(f'+++ b/{path}\n', 1)[1].split('\ndiff --git ', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))


class TitleConformancePatch(unittest.TestCase):
    def setUp(self):
        self.text = PATCH.read_text()

    def test_ordered_after_scalar_fpu_and_shipped(self):
        self.assertEqual(native.RUNTIME_PATCHES[2:4], ('patches/scalar-fpu.patch', NAME))
        for name in (NAME, 'tests/test_title_conformance.py', 'cdimage.py', 'pointer_entries.py'):
            self.assertIn(name, lab.SOURCE_FILES)

    def test_runtime_pools_leave_title_heap_range(self):
        state = added(self.text, 'ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/State.h')
        pools = {name: int(value, 16) for name, value in
                 re.findall(r'k(RpcPacket|RpcServer|Tls|BootMode)PoolBase = (0x[0-9A-Fa-f]+);', state)}
        self.assertEqual(set(pools), {'RpcPacket', 'RpcServer', 'Tls', 'BootMode'})
        # Kernel space, below the 0x100000 user load address and clear of the 0x80000 callback arena.
        self.assertTrue(all(0x40000 <= base and base + 0x10000 <= 0x80000 for base in pools.values()), pools)

    def test_cd_image_is_optional_and_absolute(self):
        runtime = added(self.text, 'ps2xRuntime/src/lib/ps2_runtime.cpp')
        self.assertIn('std::getenv("PS2_PORT_CD_IMAGE")', runtime)
        self.assertIn('is_absolute()', runtime)
        self.assertIn('defaults.cdImage', runtime)

    def test_gs_csr_reports_fifo_empty_on_every_read_path(self):
        memory = added(self.text, 'ps2xRuntime/src/lib/ps2_memory.cpp')
        self.assertIn('kGsCsrFifoEmpty = 0x4000ull', memory)
        self.assertIn('& ~0xC000ull', memory)
        self.assertEqual(memory.count('guestCsr(gs_regs.csr)'), 3)

    def test_cd_callback_is_stored_and_raised_after_reads(self):
        cd = added(self.text, 'ps2xRuntime/src/lib/Kernel/Stubs/CD.cpp')
        self.assertIn('g_cdCallback = getRegU32(ctx, 4)', cd)
        self.assertIn('GuestInvocationKind::Interrupt', cd)
        self.assertIn('queueCdCallback(runtime, kCdFuncRead)', cd)
        self.assertIn('setReturnS32(ctx, static_cast<int32_t>(previous))', cd)  # returns the old callback

    def test_semaphore_zero_max_is_unbounded(self):
        scheduler = added(self.text, 'ps2xRuntime/src/lib/Kernel/EeScheduler.cpp')
        self.assertIn('maxCount = std::numeric_limits<int>::max()', scheduler)
        self.assertNotIn('maxCount <= 0 ||', scheduler)

    def test_loadfile_fallback_only_without_an_irx_server(self):
        subsystem = added(self.text, 'ps2xIOP/src/iop_subsystem.cpp')
        self.assertIn('kLoadfileSid = 0x80000006u', subsystem)
        self.assertIn('!emulated.handled && !hle && request.sid == kLoadfileSid', subsystem)
        for function in ('kLoadfileVersion', 'kLoadfileStop', 'kLoadfileSearchName', 'kLoadfileSearchAddress'):
            self.assertIn(f'case {function}:', subsystem)
        self.assertIn('kKeUnknownModule = -202', subsystem)
        emulator = added(self.text, 'ps2xIOP/src/emulator/iop_emulator.cpp')
        self.assertIn('0x70000080u', emulator)  # names come from the IRX .iopmod section, not file names
        self.assertIn('module.resident', emulator)

    def test_sif_commands_flow_both_ways(self):
        rpc = added(self.text, 'ps2xIOP/src/emulator/services/iop_rpc.cpp')
        for ordinal in ('case 6: // sceSifGetSreg', 'case 7: // sceSifSetSreg',
                        'case 10: // sceSifAddCmdHandler', 'case 11: // sceSifRemoveCmdHandler'):
            self.assertIn(ordinal, rpc)
        self.assertIn('IopRpcBridge::deliverEeCommand', rpc)
        sif = added(self.text, 'ps2xRuntime/src/lib/Kernel/Stubs/SIF.cpp')
        self.assertIn('PS2IopTransport::deliverSifCommand', sif)  # EE -> IOP
        self.assertIn('g_sifSregs[reg] = value', sif)            # IOP -> EE SET_SREG
        self.assertIn('packetSize <= 112u', sif)

    def test_runtime_heap_reserve_stays_out_of_title_heap(self):
        system = added(self.text, 'ps2xRuntime/src/lib/Kernel/Syscalls/System.cpp')
        self.assertIn('kRuntimeHeapReserve = 0x00100000u', system)
        self.assertIn('heapLimit = g_mainThreadStackBase', system)
        self.assertIn('runtime->configureGuestHeap(runtimeBase, heapLimit)', system)
        # Only a title that placed its own main-thread stack gets the reserve; stack -1/heap -1
        # titles keep the previous layout (runtime base returned, EndOfHeap = runtime limit).
        self.assertIn('const bool titleOwnsLayout = g_mainThreadStackBase > heapBase && g_mainThreadStackBase < heapLimit;', system)
        self.assertIn('(titleOwnsLayout && heapLimit - heapBase > 2u * kRuntimeHeapReserve)', system)
        self.assertIn('setReturnU32(ctx, runtimeBase != heapBase ? heapBase : runtime->guestHeapBase());', system)
        self.assertIn('g_titleHeapEnd = runtimeBase != heapBase ? runtimeBase : 0u;', system)
        self.assertIn('g_titleHeapEnd', system)

    def test_mpeg_nodata_runs_on_calling_thread(self):
        mpeg = added(self.text, 'ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp')
        self.assertIn('kMpegCbNodata = 1u', mpeg)
        self.assertIn('scheduler.invokeCurrent(std::move(invocation))', mpeg)
        self.assertIn('GuestInvocationKind::HleCall', mpeg)
        self.assertIn('kMpegEndOnlyLimit = 60u', mpeg)
        self.assertIn('g_mpegFedStreamEnded[mpegAddr]', mpeg)

    def test_vu_max_mini_compare_raw_bits(self):
        vu = added(self.text, 'ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        self.assertIn('static inline float vuMaxBits(float a, float b)', vu)
        self.assertIn('(ia < 0 && ib < 0) ? std::min(ia, ib) : std::max(ia, ib)', vu)  # sign-magnitude order
        for operands in ('m_state.vf[fs][c], m_state.vf[ft][c]', 'm_state.vf[fs][c], m_state.i', 'm_state.vf[fs][c], bc'):
            self.assertIn(f'vuMaxBits({operands})', vu)
            self.assertIn(f'vuMinBits({operands})', vu)
        self.assertIn('vu.m_state.i = immediate;', vu)  # I-bit immediate keeps its raw bits

    def test_vu_branch_vi_backup_rules(self):
        vu = added(self.text, 'ps2xRuntime/src/lib/vu/ps2_vu1_impl.inl')
        self.assertEqual(vu.count('(uint16_t)m_state.vi[is] * 8u'), 2)  # JR/JALR ignore the backup
        # Stalls keep the backup (PCSX2 interpreter; microVU's opposite rule is a stated assumption).
        self.assertNotIn('vu.m_viBranchBackupValid = false;\n            while (readyCycle', vu)
        tests = added(self.text, 'ps2xTest/src/ps2_vu1_tests.cpp')
        self.assertIn('JR reads the current VI, not the conditional-branch VI backup', tests)
        self.assertIn('prevBackupValid && prevBackupReg == writtenVi ? prevBackupValue : oldVi', vu)  # oldest wins

    def test_path3_unmask_pulse_releases_one_packet(self):
        vif = added(self.text, 'ps2xRuntime/src/lib/ps2_vif1_interpreter.cpp')
        self.assertIn('releaseOneMaskedPath3Packet();', vif)
        self.assertIn('opcode > VIF_MARK && opcode != 0x20u && opcode != 0x30u && opcode != 0x31u', vif)
        memory = added(self.text, 'ps2xRuntime/src/lib/ps2_memory.cpp')
        self.assertIn('void PS2Memory::releaseOneMaskedPath3Packet()', memory)
        self.assertIn('const bool eop = (tag & 0x8000u) != 0u;', memory)
        self.assertIn('std::getenv("PS2_PORT_PATH3_PULSE")', memory)

    def test_alarm_syscalls_use_patched_kernel_numbering(self):
        dispatcher = added(self.text, 'ps2xRuntime/src/lib/Kernel/Syscalls/Dispatcher.cpp')
        self.assertIn('case 0xFD:\n            ReleaseAlarm(rdram, ctx, runtime);', dispatcher)
        self.assertIn('case static_cast<uint32_t>(-0xFE):', dispatcher)

    def test_started_threads_inherit_vu0_state_with_constant_vf0(self):
        scheduler = added(self.text, 'ps2xRuntime/src/lib/Kernel/EeScheduler.cpp')
        self.assertIn('std::memcpy(vu.vu0_vf, caller.vu0_vf, sizeof(vu.vu0_vf));', scheduler)
        self.assertIn('vu.vu0_vf[0] = _mm_set_ps(1.0f, 0.0f, 0.0f, 0.0f);', scheduler)
        self.assertIn('vu.vi[0] = 0;', scheduler)

    def test_public_patch_carries_no_title_code_or_private_paths(self):
        self.assertNotIn('/Users/', self.text)
        found = re.search(r'(?i)\b(?:ssx|kelly|slater)|\b(?:SLUS|SLES|SCES|SCUS|SLPS|SLPM)[_-]?\d', self.text)
        self.assertIsNone(found, found and found.group(0))
        lines = [line for line in self.text.splitlines() if line.startswith('+')]
        # Only hardware/SDK constants and bit masks: no guest code addresses in the user program range.
        addresses = {int(v, 16) for line in lines for v in re.findall(r'0x([0-9A-Fa-f]{6,8})', line)}
        self.assertFalse({a for a in addresses if 0x100000 <= a < 0x2000000 and a & (a + 1) and a != 0x100000}, addresses)

    @unittest.skipUnless(lab.SOURCE.joinpath('.git').exists() and shutil.which('git'), 'shared PS2Recomp checkout not present')
    def test_applies_over_pinned_source_after_the_earlier_patches(self):
        archive = subprocess.run(['git', '-C', str(lab.SOURCE), 'archive', 'HEAD', 'ps2xRuntime', 'ps2xIOP',
                                  'ps2xRecomp', 'ps2xTest'], capture_output=True, check=True).stdout
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter='data')
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            for name in native.RUNTIME_PATCHES:
                applied = subprocess.run(['git', '-C', tmp, 'apply', str(ROOT / name)], capture_output=True, text=True)
                self.assertEqual(applied.returncode, 0, f'{name}: {applied.stderr}')


if __name__ == '__main__':
    unittest.main()
