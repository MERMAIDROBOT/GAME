"""Bounded compiled VU0 idle-loop differential; synthetic programs only.

Run from the engine root with its normal toolchain environment:
    python3 tests/check_vu0_poll.py OUTPUT_DIRECTORY
The control removes only the emitted idle shortcut, leaving the same compiler,
instruction handlers, cycle budget and slice boundaries on both sides.
"""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vu_micro_check as vm
from compute_slot import assert_idle, slot


def check(work):
    work.mkdir(parents=True)
    images, cases = {}, []
    for backward in (False, True):
        for reg in (1, 15):
            for swapped in (False, True):
                for side_effect in (False, True):
                    # Exit sets vi2=7, then ends. A backward exit starts at pair2.
                    end = [(0x10020007, 0x400002ff), (0x8000033c, 0x2ff)]
                    branch = 0x50000000 | (reg << (16 if swapped else 11))
                    branch |= 0x7fd if backward else 3
                    delay = 0x10030001 if side_effect else 0x8000033c
                    loop = [(branch, 0x2ff), (delay, 0x2ff),
                            (0x400007fd, 0x2ff), (0x8000033c, 0x2ff)]
                    image = bytearray(4096)
                    for pos, pair in enumerate(end + loop if backward else loop + end):
                        struct.pack_into('<II', image, pos * 8, *pair)
                    batch = len(images)
                    images[('vu0', batch)] = image
                    cases.append((batch, 16 if backward else 0, reg, side_effect))
    sources = vm.compile_programs(work, images)
    marker = (r'^        if \(!vu\.m_viBranchBackupValid && cyc >= vu\.m_viReady\[\d+\].*\n'
              r'            cyc \+= .*\n')
    controls, shortcuts = [], 0
    for source in sources:
        text, count = re.subn(marker, '', source.read_text(), flags=re.M)
        shortcuts += count
        output = work / source.name
        output.write_text(text)
        controls.append(output)
    # Eight pure loops qualify; eight delay-slot side-effect controls must not.
    assert shortcuts == 8, shortcuts
    harness = vm.HARNESS.split('int main(')[0] + r'''
int main(int argc, char **argv) {
    std::vector<uint8_t> code(4096), data(4096);
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(code.data(), 1, code.size(), f) != code.size()) return 1;
    fclose(f);
    unsigned start = std::stoul(argv[2]), reg = std::stoul(argv[3]);
    registerGeneratedVu1Programs();
    auto *mem = reinterpret_cast<PS2Memory *>(g_memory);
    auto &gs = *reinterpret_cast<GS *>(g_gs);
    for (unsigned budget : {1u,2u,3u,4u,5u,6u,7u,8u,9u,10u,11u,12u,63u,64u,65u,4093u,4096u,4097u}) {
        for (unsigned peer : {0u,1u,0xffffu}) {
            VU1Interpreter vu(VU1Interpreter::Unit::VU0);
            vu.state().vi[reg] = peer;
            auto step = [&](bool first) {
                if (first) vu.execute(code.data(),4096,data.data(),4096,gs,mem,0,0,start,budget);
                else vu.resume(code.data(),4096,data.data(),4096,gs,mem,0,0,budget);
                const auto &s=vu.state();
                printf("%u %u %u %u %u %u %u %llu", s.pc,vu.running(),s.branchPending,
                       s.branchTarget,s.branchDelay,s.status,s.mac,
                       static_cast<unsigned long long>(s.cycles));
                for (unsigned i=0;i<16;++i) printf(" %u",s.vi[i]);
                puts("");
            };
            step(true);
            for (unsigned n=0;n<3 && vu.running();++n) step(false);
            vu.state().vi[reg]=0;
            for (unsigned n=0;n<16 && vu.running();++n) step(false);
            if (vu.running() || vu.state().vi[2]!=7) return 3;
        }
    }
}
'''
    (work / 'harness.cpp').write_text(harness)
    flags, runtime = vm._flags(work)
    outputs = {}
    for name, selected in (('control', controls), ('optimized', sources)):
        app = work / name
        subprocess.run(flags + ['-DVU_MICRO_COMPILED', str(work / 'harness.cpp'),
                       str(runtime / 'src/lib/vu/ps2_vu1_core.cpp'),
                       str(runtime / 'src/lib/vu/ps2float.cpp'),
                       *(str(p) for p in selected), '-Wl,-undefined,dynamic_lookup',
                       '-o', str(app)], check=True)
        records = []
        for batch, start, reg, _ in cases:
            records.append(subprocess.check_output([str(app), str(work / f'capture/vu0_{batch:04d}.bin'),
                                                    str(start), str(reg)], timeout=30))
        outputs[name] = b''.join(records)
        (work / f'{name}.txt').write_bytes(outputs[name])
    assert outputs['control'] == outputs['optimized'], 'Idle shortcut changed a slice-boundary state'
    result = dict(status='pass', programs=len(cases), peer_budget_cases=len(cases)*54,
                  identical_state_records=len(outputs['control'].splitlines()), shortcuts=shortcuts)
    (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    with slot('vu0-idle-loop-differential'):
        assert_idle()
        check(Path(sys.argv[1]).resolve())
