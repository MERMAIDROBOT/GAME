"""VU1 lockstep check: compiled C++ VU programs against the runtime's cycle-level interpreter.

Random VU1 microprograms mix FMAC work (register hazards and stalls), loads and stores, Q and P unit
operations with their waits, flag reads, forward branches and XGKICK transfers of prepared GIF
packets, so stores race the PATH1 transfer that reads the same memory. Each program is compiled by
the engine's vector compiler (tools/vu1_recomp.cpp) and run through the runtime's own lockstep
verifier (VU1InterpreterAccess::runVerifiedWith), which runs the compiled program and the interpreter
from the same state and compares registers, flags, pipeline and XGKICK state, cycle counts, data
memory and every GIF packet sent. Programs run to completion in one call and also in random cycle
budgets, as the runtime slices VU1 work between EE steps.

This checks that the C++ the engine runs keeps the interpreter's timing. The interpreter's own timing
is checked against PS2 hardware results by vu_hardware_check.py where hardware results exist.
"""
import json
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import behaviour_check as bc
import vu_micro_check as vm

CODE, DATA = 16384, 16384
PACKET_QWORDS = (0x100, 0x180, 0x200, 0x280)  # where prepared GIF packets start
KICK_VI = (2, 3, 4, 5)  # VI registers holding those addresses
STORE_BASES = ((6, 0x320), (7, 0x360))  # VI registers every other store goes through, away from the packets
FREE_VI = range(8, 16)  # integer results, link registers and branch conditions
STORE_BASE = {'sq': 'vt', 'vsqi': 'vt', 'vsqd': 'vt', 'isw': 'vs', 'viswr': 'vs'}
MODIFIED_BASE = {'vlqi': 'vs', 'vlqd': 'vs'}
INT_DEST = {'ilw': 'vt', 'iaddiu': 'vt', 'isubiu': 'vt', 'viadd': 'vd', 'visub': 'vd', 'viand': 'vd', 'vior': 'vd',
            'viaddi': 'vt', 'vmtir': 'vt', 'fsand': 'vt', 'fmand': 'vt', 'fcget': 'vt', 'xtop': 'vt', 'xitop': 'vt',
            'vilwr': 'vt'}
UPPER = [n for n in bc.VU_FORMS if n not in vm.MACRO_LOWER and n not in vm.EE_ONLY]
LOWER_MACRO = ['vdiv', 'vsqrt', 'vrsqrt', 'vmove', 'vmr32', 'vmtir', 'vmfir', 'viadd', 'visub', 'viaddi', 'viand',
               'vior', 'vlqi', 'vsqi', 'vlqd', 'vsqd', 'vilwr', 'viswr']
LOWER_MICRO = ['lq', 'sq', 'ilw', 'isw', 'iaddiu', 'isubiu', 'fcand', 'fcset', 'fsand', 'fsset', 'fmand', 'fcget',
               'mfp', 'xtop', 'xitop', 'rnext', 'rget', 'esadd', 'ersqrt', 'ercpr', 'esqrt', 'esin', 'eatan', 'eexp', 'esum']
XGKICK = 0x6C
SPECIAL = (0x00000005, 0x80000005, 0x007FFFFF, 0x00800000, 0x7FFFFFFF, 0xFFFFFFFF, 0x80000000, 0x7F800000, 0x3F800000)
HARNESS = r'''
#include "ps2_vu1_impl.inl"
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
void registerGeneratedVu1Programs();
alignas(64) static unsigned char g_gs[sizeof(GS)];
static std::vector<uint8_t> load(const std::string &path, size_t size) {
    std::vector<uint8_t> out(size);
    FILE *f = std::fopen(path.c_str(), "rb");
    if (!f || std::fread(out.data(), 1, size, f) != size) std::exit(2);
    std::fclose(f);
    return out;
}
static uint64_t fnv(const std::vector<uint8_t> &d) {
    uint64_t h = 0xCBF29CE484222325ull;
    for (uint8_t b : d) h = (h ^ b) * 0x100000001B3ull;
    return h;
}
int main(int argc, char **argv) {
    registerGeneratedVu1Programs();
    const std::string dir = argv[1];
    const int count = std::atoi(argv[2]);
    for (int p = 0; p < count; ++p) {
        auto code = load(dir + "/vu1_" + std::to_string(p) + ".bin", 16384);
        const auto data0 = load(dir + "/data_" + std::to_string(p) + ".bin", 16384);
        const auto regs = load(dir + "/regs_" + std::to_string(p) + ".bin", 512 + 64);
        const auto program = findCompiledVu1ProgramByHash(fnv(code));
        if (!program) { std::printf("P %d nocompiled\n", p); continue; }
        FILE *sf = std::fopen((dir + "/slices_" + std::to_string(p) + ".txt").c_str(), "r");
        std::vector<uint32_t> budgets;
        for (unsigned b; sf && std::fscanf(sf, "%u", &b) == 1;) budgets.push_back(b);
        if (sf) std::fclose(sf);
        for (size_t mode = 0; mode < 2; ++mode) {
            std::vector<uint8_t> data = data0;
            VU1Interpreter vu(VU1Interpreter::Unit::VU1);
            vu.reset();
            VU1State &s = vu.state();
            std::memcpy(s.vf, regs.data(), 512);
            s.vf[0][0] = s.vf[0][1] = s.vf[0][2] = 0.0f; s.vf[0][3] = 1.0f;
            for (int k = 0; k < 16; ++k) { int32_t v; std::memcpy(&v, regs.data() + 512 + 4 * k, 4); s.vi[k] = k ? (int32_t)(int16_t)v : 0; }
            s.pc = 0;
            bool ok = true;
            int calls = 0;
            for (;;) {
                const uint32_t budget = mode == 0 ? 200000u : budgets[calls % budgets.size()];
                ok = VU1InterpreterAccess::runVerifiedWith(vu, code.data(), 16384u, data.data(), 16384u,
                                                           *reinterpret_cast<GS *>(g_gs), nullptr, budget, program, false) && ok;
                ++calls;
                if (!vu.running()) break;  // ended (E bit, halt or error), not merely out of budget
                if (calls >= 20000) { ok = false; break; }
            }
            if (mode == 0 && calls != 1) ok = false;
            uint64_t h = 0xCBF29CE484222325ull;
            const auto mix = [&h](const void *bytes, size_t size)
            {
                for (size_t i = 0; i < size; ++i) h = (h ^ static_cast<const uint8_t *>(bytes)[i]) * 0x100000001B3ull;
            };
            mix(s.vf, sizeof(s.vf)); mix(s.vi, sizeof(s.vi)); mix(s.acc, sizeof(s.acc)); mix(&s.q, 4); mix(&s.p, 4);
            mix(&s.mac, 4); mix(&s.status, 4); mix(&s.clip, 4); mix(data.data(), data.size());
            std::printf("P %d %s %s calls=%d state=%016llx cycles=%llu\n", p, mode ? "sliced" : "whole", ok ? "ok" : "MISMATCH", calls,
                        (unsigned long long)h, (unsigned long long)s.cycles);
        }
    }
    return 0;
}
'''


def _lower(rng, name, fields):
    if name in vm.MACRO_LOWER:
        return 0x80000000 | (bc.VU_FORMS[name]['encode'](fields) & 0x01FFFFFF)
    return vm.MICRO_FORMS[name][0](fields)


def program(rng, length):
    """Code image words and data memory for one random program."""
    pointers = list(zip(KICK_VI, PACKET_QWORDS)) + list(STORE_BASES)
    pairs = [((0x08 << 25) | (reg << 16) | qw, vm.NOP_UPPER) for reg, qw in pointers]
    body = []
    for position in range(length):
        upper = vm.NOP_UPPER
        if rng.random() < 0.6:
            name = rng.choice(UPPER)
            upper = bc.VU_FORMS[name]['encode'](vm._fields(rng, name, rng.randrange(3))) & 0x01FFFFFF
        roll = rng.random()
        if roll < 0.30:
            lower = vm.NOP_LOWER
        elif roll < 0.42:
            reg = rng.choice(KICK_VI)
            lower = vm._lower_special(XGKICK, {'vs': reg})
        elif roll < 0.52:
            # A store into a prepared packet: payload races the transfer that reads it.
            reg = rng.choice(KICK_VI)
            f = vm._fields(rng, 'sq', 0)
            f.update(vt=reg, imm=rng.randrange(1, 6), dest=rng.randrange(1, 16))
            lower = vm.MICRO_FORMS['sq'][0](f)
        elif roll < 0.56:
            lower = vm.WAITQ_LOWER
        elif roll < 0.60:
            lower = vm.WAITP_LOWER
        elif roll < 0.65 and position < length - 2:
            # Forward branch over the next pair; condition registers avoid the packet pointers.
            op = rng.choice(list(vm.BRANCH_FORMS.values()))[0]
            vs, vt = rng.sample(FREE_VI, 2)
            body.append(((op << 25) | (vt << 16) | (vs << 11) | 2, upper))
            continue
        else:
            name = rng.choice(LOWER_MACRO + LOWER_MICRO)
            f = vm._fields(rng, name, rng.randrange(3))
            # Only the dedicated pointers move through memory, so no store can corrupt a GIFtag.
            if name in STORE_BASE:
                f[STORE_BASE[name]] = rng.choice(STORE_BASES)[0]
                f['imm'] = rng.randrange(16)
            if name in MODIFIED_BASE:
                f[MODIFIED_BASE[name]] = rng.choice(FREE_VI)
            if name in INT_DEST:
                f[INT_DEST[name]] = rng.choice(FREE_VI)
            if name in ('iaddiu', 'isubiu'):
                f.update(dest=0, imm=rng.randrange(32))
            lower = _lower(rng, name, f)
        body.append((lower, upper))
    pairs += body
    pairs += [(vm.NOP_LOWER, vm.NOP_UPPER | vm.E_BIT), (vm.NOP_LOWER, vm.NOP_UPPER)]
    assert 8 * len(pairs) <= CODE
    end = 8 * (len(pairs) - 2)
    code = bytearray(CODE)
    for k, (lower, upper) in enumerate(pairs):
        struct.pack_into('<2I', code, 8 * k, lower, upper)
    data = bytearray(rng.getrandbits(8) for _ in range(DATA))
    for qw in PACKET_QWORDS:
        _packet(rng, data, qw)
    return code, data, end


def _tag(rng, qwords, eop):
    """A GIFtag (PACKED, REGLIST or IMAGE) whose payload is exactly `qwords` qwords."""
    flg = rng.randrange(3)
    if flg == 0:
        nreg = rng.choice([n for n in (1, 2, 3, 4) if qwords % n == 0] or [1])
        nloop = qwords // nreg
    elif flg == 1:
        nreg, nloop = 2, qwords
    else:
        nreg, nloop = 1, qwords
    return nloop | (eop << 15) | (flg << 58) | ((nreg & 15) << 60)


def _packet(rng, data, qword):
    """A GIFtag with five payload qwords (where the racing stores land), then an EOP tag."""
    struct.pack_into('<QQ', data, 16 * qword, _tag(rng, 5, 0), 0x4E41E)
    struct.pack_into('<QQ', data, 16 * (qword + 6), _tag(rng, rng.randrange(5), 1), 0x4E41E)


def registers(rng):
    # Floats, raw bit patterns, and the small integers FTOI leaves in registers (denormal as floats).
    vf = b''.join(struct.pack('<I', rng.choice([bc.random_float(rng), rng.getrandbits(32), rng.randrange(-300, 300) & bc.M32,
                                                rng.choice(SPECIAL)])) for _ in range(128))
    vi = b''.join(struct.pack('<i', rng.randrange(0x300, 0x3F0)) for _ in range(16))
    return vf + vi


def operand_cases():
    """MAX and MINI forms (broadcast, I, vector) over special operand pairs, one short program each."""
    items = []
    for op in (0x11, 0x15, 0x1D, 0x1F, 0x2B, 0x2F):
        for a in SPECIAL:
            for b in SPECIAL:
                upper = (0xF << 21) | (31 << 16) | (2 << 11) | (29 << 6) | op
                pairs = [(vm.NOP_LOWER, upper)] + [(vm.NOP_LOWER, vm.NOP_UPPER)] * 6
                pairs += [(vm.NOP_LOWER, vm.NOP_UPPER | vm.E_BIT), (vm.NOP_LOWER, vm.NOP_UPPER)]
                code = bytearray(CODE)
                for k, (lower, up) in enumerate(pairs):
                    struct.pack_into('<2I', code, 8 * k, lower, up)
                vf = [0] * 128
                vf[8:12], vf[124:128] = [a] * 4, [b] * 4
                items.append(dict(code=bytes(code), data=bytes(DATA), end=8 * (len(pairs) - 2),
                                  regs=struct.pack('<128I', *vf) + bytes(64), slices=[3, 5]))
    return items


def xgkick_cases():
    """XGKICK, then after 0-12 cycles a store into payload qword 1-5 of the packet being sent: whether
    the packet carries the old or the new data depends on when PATH1 reads that qword."""
    items = []
    rng = random.Random(0)
    for delay in range(13):
        for qword in range(1, 6):
            pairs = [((0x08 << 25) | (2 << 16) | PACKET_QWORDS[0], vm.NOP_UPPER), (vm.NOP_LOWER, vm.NOP_UPPER),
                     (vm._lower_special(XGKICK, {'vs': 2}), vm.NOP_UPPER)]
            pairs += [(vm.NOP_LOWER, vm.NOP_UPPER)] * delay
            pairs += [(vm.MICRO_FORMS['sq'][0](dict(vs=1, vt=2, imm=qword, dest=0xF)), vm.NOP_UPPER)]
            pairs += [(vm.NOP_LOWER, vm.NOP_UPPER)] * 4
            pairs += [(vm.NOP_LOWER, vm.NOP_UPPER | vm.E_BIT), (vm.NOP_LOWER, vm.NOP_UPPER)]
            code = bytearray(CODE)
            for k, (lower, upper) in enumerate(pairs):
                struct.pack_into('<2I', code, 8 * k, lower, upper)
            data = bytearray(rng.getrandbits(8) for _ in range(DATA))
            struct.pack_into('<QQ', data, 16 * PACKET_QWORDS[0], 5 | (2 << 58) | (1 << 60), 0)  # IMAGE, 5 qwords
            struct.pack_into('<QQ', data, 16 * (PACKET_QWORDS[0] + 6), 1 << 15, 0)  # empty EOP tag
            vf = [0] * 128
            vf[4:8] = [0x11111111, 0x22222222, 0x33333333, 0x44444444]
            items.append(dict(code=bytes(code), data=bytes(data), end=8 * (len(pairs) - 2),
                              regs=struct.pack('<128I', *vf) + bytes(64), slices=[1, 2, 3]))
    return items


def regression_cases():
    """Shrunk random programs where the compiled code and the interpreter once disagreed."""
    import base64
    import zlib
    fixture = json.loads((Path(__file__).resolve().parent / 'tests/fixtures/vu-lockstep-regressions.json').read_text())
    unpack = lambda text: zlib.decompress(base64.b64decode(text))
    return [dict(code=unpack(c['code']), data=unpack(c['data']), regs=unpack(c['regs']), end=c['end'], slices=c['slices'])
            for c in fixture['cases']]


def generate(rng, programs, length):
    items = []
    for _ in range(programs):
        code, data, end = program(rng, length)
        items.append(dict(code=bytes(code), data=bytes(data), end=end, regs=registers(rng),
                          slices=[rng.randrange(1, 64) for _ in range(64)]))
    return items


def run(items, work, mutate=None):
    """Each item through the lockstep verifier, whole and sliced: [(whole ok, sliced ok)], mismatch lines."""
    flags, runtime = vm._flags(work)
    vu_dir = runtime / 'src/lib/vu'
    if mutate:
        copy = work / 'vu-src'
        shutil.copytree(vu_dir, copy, dirs_exist_ok=True)
        target = copy / mutate[0]
        text = target.read_text()
        if mutate[1] not in text:
            raise ValueError('mutation target not found')
        target.write_text(text.replace(mutate[1], mutate[2]))
        vu_dir = copy
    # The (possibly mutated) VU sources must win over the runtime's own include directory.
    flags = [str(vu_dir) if f == str(runtime / 'src/lib/vu') else f for f in flags]
    inputs = work / 'inputs'
    shutil.rmtree(inputs, ignore_errors=True)
    inputs.mkdir(parents=True)
    images = {}
    for p, item in enumerate(items):
        images[('vu1', p)] = item['code']
        (inputs / f'vu1_{p}.bin').write_bytes(item['code'])
        (inputs / f'data_{p}.bin').write_bytes(item['data'])
        (inputs / f'regs_{p}.bin').write_bytes(item['regs'])
        (inputs / f'slices_{p}.txt').write_text(' '.join(map(str, item['slices'])))
    sources = _compile(work, images, flags, vu_dir)
    (work / 'harness.cpp').write_text(HARNESS)
    command = flags + ['-I', str(vu_dir), str(work / 'harness.cpp'), str(vu_dir / 'ps2_vu1_core.cpp'),
                       str(vu_dir / 'ps2float.cpp'), *map(str, sources), '-Wl,-undefined,dynamic_lookup', '-o',
                       str(work / 'lockstep')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if built.returncode:
        raise RuntimeError('lockstep harness did not compile:\n' + built.stderr[-4000:])
    ran = subprocess.run([str(work / 'lockstep'), str(inputs), str(len(items)), *(str(i['end']) for i in items)],
                         capture_output=True, text=True, timeout=1800)
    if ran.returncode:
        raise RuntimeError('lockstep harness failed:\n' + ran.stderr[-3000:])
    results = [[None, None] for _ in items]
    states = [[None, None] for _ in items]
    cycles = 0
    for line in ran.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == 'P':
            p, sliced = int(parts[1]), parts[2] == 'sliced'
            results[p][sliced] = parts[3] == 'ok'
            states[p][sliced] = line.split('state=')[1].split()[0] if 'state=' in line else None
            cycles += int(line.split('cycles=')[1]) if 'cycles=' in line else 0
    # Running in budget slices must end in the same state as one long run. (The VU's own cycle
    # count may end a few cycles later: about one per slice boundary.)
    for p, (whole, sliced) in enumerate(states):
        if whole is None or whole != sliced:
            results[p][1] = False
    return results, [l for l in ran.stderr.splitlines() if 'mismatch #' in l], cycles


def check(programs=24, length=80, seed=1, work=None, mutate=None):
    """mutate: (path, old, new) applied to a copy of the runtime's VU sources (negative control)."""
    rng = random.Random(seed)
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='vu-lockstep-')) if owned else Path(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        items = generate(rng, programs, length)
        results, details, cycles = run(items, work, mutate)
        failing = [p for p, r in enumerate(results) if r != [True, True]]
        return dict(passed=not failing, programs=programs, runs=2 * programs, failing=failing[:20],
                    details=details[:20], cycles=cycles)
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)


PROLOGUE = len(KICK_VI) + len(STORE_BASES)


def _pairs(code, end):
    return [struct.unpack_from('<2I', code, 8 * k) for k in range(end // 8)]


def _with_nops(item, removed):
    code = bytearray(item['code'])
    for k in removed:
        struct.pack_into('<2I', code, 8 * k, vm.NOP_LOWER, vm.NOP_UPPER)
    return dict(item, code=bytes(code))


def shrink(item, work, rounds=12):
    """Body pairs whose removal keeps the program mismatching are replaced by NOP pairs (greedy, batched)."""
    body = list(range(PROLOGUE, item['end'] // 8))
    removed = set()
    for _ in range(rounds):
        live = [k for k in body if k not in removed]
        chunk = max(1, len(live) // 8)
        candidates = [set(live[i:i + chunk]) for i in range(0, len(live), chunk)]
        if chunk > 1:
            candidates += [{k} for k in live]
        variants = [_with_nops(item, removed | c) for c in candidates]
        results, _, _ = run(variants, work)
        progress = False
        for c, r in zip(candidates, results):
            if r != [True, True] and not (c & removed):
                removed |= c
                progress = True
        # Combined removals may interact; confirm and back off to the largest single success.
        confirm, _, _ = run([_with_nops(item, removed)], work)
        if confirm[0] == [True, True]:
            ok = [c for c, r in zip(candidates, results) if r != [True, True]]
            removed = (removed - set().union(*candidates)) | (max(ok, key=len) if ok else set())
        if not progress:
            break
    left = [k for k in body if k not in removed]
    return _with_nops(item, removed), [(k, *_pairs(item['code'], item['end'])[k]) for k in left]


def _compile(work, images, flags, vu_dir):
    tool = work / 'vu1_recomp'
    command = [c for c in flags] + ['-I', str(vu_dir), str(Path(__file__).resolve().parent / 'tools/vu1_recomp.cpp'),
                                    str(vu_dir / 'ps2_vu1_core.cpp'), str(vu_dir / 'ps2float.cpp'),
                                    '-Wl,-undefined,dynamic_lookup', '-o', str(tool)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if built.returncode:
        raise RuntimeError('Vector compiler did not build:\n' + built.stderr[-3000:])
    capture, generated = work / 'capture', work / 'compiled'
    capture.mkdir(exist_ok=True)
    generated.mkdir(exist_ok=True)
    for (unit, index), image in images.items():
        (capture / f'{unit}_{index:04d}.bin').write_bytes(bytes(image))
    ran = subprocess.run([str(tool), str(capture), str(generated)], capture_output=True, text=True, timeout=900)
    if ran.returncode:
        raise RuntimeError('Vector compiler failed:\n' + (ran.stdout + ran.stderr)[-3000:])
    return sorted(generated.glob('*.cpp'))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--programs', type=int, default=24)
    parser.add_argument('--length', type=int, default=80)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--work')
    args = parser.parse_args(argv)
    report = check(args.programs, args.length, args.seed, args.work)
    print(json.dumps(report, indent=1))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
