"""PS2 hardware results for VU programs, replayed through the runtime's VU interpreter.

The programs of unknownbrackets/ps2autotests tests/vu/lower/{efu,fdivdelay,branch} are transcribed
here from that suite's sources (its encodings, `Wr` pairing and exit sequence); the downloaded code
itself is never built or run. Their outputs on a real PS2 are in tests/fixtures/vu-hardware.json.
Each program runs from pc 0 of its own code image through vu_micro_check's harness.

EFU: ERCPR, ESQRT, ERSQRT, ESUM, ESADD, ERSADD, ELENG and ERLENG must match exactly; EATAN*, ESIN
and EEXP are reported as exact, within a few last-bit steps, or wrong (their hardware internals are
not public). FDIV delay and JALR must match exactly.
"""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import behaviour_check as bc
import vu_micro_check as vm

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / 'tests/fixtures/vu-hardware.json'

# ---- encodings (ps2autotests tests/vu/assemble/ops.cpp, encoding.h) ----------------------------------
X, Y, Z, W = 0, 1, 2, 3
DEST_W, DEST_XYZ, DEST_XYZW = 0x1, 0xE, 0xF
UPPER_I, UPPER_E = 1 << 31, 1 << 30
UPPER_NOP = 0x2FF
LOWER_NOP = 0x80000030  # IADD vi00, vi00, vi00
LOWER = 0x80000000


def upper1(op, dest, d, s, t=0):
    return (dest << 21) | (t << 16) | (s << 11) | (d << 6) | op


def upper3(op, dest, s, t):
    return (dest << 21) | (t << 16) | (s << 11) | op


def lower3(op, dest, s, t):
    return LOWER | (dest << 21) | (t << 16) | (s << 11) | op


def lower4(op, fsf, s, ftf, t):
    return LOWER | (ftf << 23) | (fsf << 21) | (t << 16) | (s << 11) | op


def MULq(dest, d, s): return upper1(0x1C, dest, d, s)
def MULi(dest, d, s): return upper1(0x1E, dest, d, s)
def MADD(dest, d, s, t): return upper1(0x29, dest, d, s, t)
def CLIP(s, t): return upper3(0x1FF, DEST_XYZ, s, t)
def DIV(fsf, s, ftf, t): return lower4(0x3BC, fsf, s, ftf, t)
def SQRT(ftf, t): return lower4(0x3BD, 0, 0, ftf, t)
def RSQRT(fsf, s, ftf, t): return lower4(0x3BE, fsf, s, ftf, t)
def WAITQ(): return lower3(0x3BF, 0, 0, 0)
def WAITP(): return lower3(0x7BF, 0, 0, 0)
def MFP(dest, t): return lower3(0x67C, dest, 0, t)
def IADDI(t, s, imm5): return LOWER | (t << 16) | (s << 11) | ((imm5 & 0x1F) << 6) | 0x32
def JALR(t, s): return (0x25 << 25) | (t << 16) | (s << 11)
def B(offset): return (0x20 << 25) | (offset & 0x7FF)
def IBLTZ(s, offset): return (0x2C << 25) | (s << 11) | (offset & 0x7FF)


EFU_OPS = {  # name: (opcode, scalar)
    'EATAN': (0x7FD, True), 'EATANxy': (0x77C, False), 'EATANxz': (0x77D, False), 'EEXP': (0x7FE, True),
    'ELENG': (0x73E, False), 'ERCPR': (0x7BE, True), 'ERLENG': (0x73F, False), 'ERSADD': (0x73D, False),
    'ERSQRT': (0x7BD, True), 'ESADD': (0x73C, False), 'ESIN': (0x7FC, True), 'ESQRT': (0x7BC, True),
    'ESUM': (0x77E, False),
}
VECTOR_DEST = {'EATANxy': 0xC, 'EATANxz': 0xA, 'ELENG': DEST_XYZ, 'ERLENG': DEST_XYZ, 'ERSADD': DEST_XYZ,
               'ESADD': DEST_XYZ, 'ESUM': DEST_XYZW}
EXACT_EFU = {'ERCPR', 'ESQRT', 'ERSQRT', 'ESUM', 'ESADD', 'ERSADD', 'ELENG', 'ERLENG'}


class Block:
    """Pairs as ps2autotests' Block writes them: an upper op pairs with a lower NOP and vice versa."""
    def __init__(self):
        self.pairs = []

    def wr(self, upper=UPPER_NOP, lower=LOWER_NOP):
        self.pairs.append((lower, upper))
        return len(self.pairs) - 1

    def wr_imm(self, value, upper=UPPER_NOP):
        self.pairs.append((struct.unpack('<I', struct.pack('<f', value))[0], upper | UPPER_I))

    def code(self):
        pairs = self.pairs + [(LOWER_NOP, UPPER_NOP | UPPER_E), (LOWER_NOP, UPPER_NOP)]
        return b''.join(struct.pack('<2I', lower, upper) for lower, upper in pairs)


# ---- programs ---------------------------------------------------------------------------------------
def efu_program(name):
    op, scalar = EFU_OPS[name]
    block = Block()
    if scalar:
        lower = LOWER | (Z << 21) | (1 << 11) | op  # field Z of VF01
    else:
        lower = LOWER | (VECTOR_DEST[name] << 21) | (1 << 11) | op
    block.wr(lower=lower)
    block.wr(lower=WAITP())
    block.wr(lower=MFP(DEST_XYZW, 2))
    return block


FDIV = {'DIV': lambda t: DIV(W, 0, W, t), 'SQRT': lambda t: SQRT(W, t), 'RSQRT': lambda t: RSQRT(W, 0, W, t)}


def _fdiv_prologue(block, two=False):
    block.wr(lower=DIV(W, 0, W, 0))  # Q = 1.0
    block.wr(lower=WAITQ())
    block.wr_imm(4.0)
    if two:
        block.wr_imm(16.0, MULi(DEST_W, 16, 0))
        block.wr(MULi(DEST_W, 17, 0))
    else:
        block.wr(MULi(DEST_W, 16, 0))


def _mulq_run(block, first, last):
    for reg in range(first, last + 1):
        block.wr(MULq(DEST_XYZW, reg, 0))


def fdiv_program(name, variant):
    func = FDIV[name]
    block = Block()
    if variant == 'single waitq':
        _fdiv_prologue(block)
        block.wr(MULq(DEST_XYZW, 1, 0), func(16))
        block.wr(MULq(DEST_XYZW, 2, 0), WAITQ())
        block.wr(MULq(DEST_XYZW, 3, 0))
    elif variant == 'multiple waitq':
        _fdiv_prologue(block, two=True)
        block.wr(MULq(DEST_XYZW, 1, 0), func(16))
        block.wr(MULq(DEST_XYZW, 2, 0), WAITQ())
        block.wr(MULq(DEST_XYZW, 3, 0))
        block.wr(MULq(DEST_XYZW, 4, 0), func(17))
        block.wr(MULq(DEST_XYZW, 5, 0), WAITQ())
        block.wr(MULq(DEST_XYZW, 6, 0))
    elif variant == 'single delayed':
        _fdiv_prologue(block)
        block.wr(lower=func(16))
        _mulq_run(block, 1, 14)
    elif variant == 'multiple delayed (1)':
        _fdiv_prologue(block, two=True)
        block.wr(lower=func(16))
        block.wr(lower=func(17))
        _mulq_run(block, 1, 14)
    elif variant == 'multiple delayed (2)':
        _fdiv_prologue(block, two=True)
        block.wr(lower=func(16))
        block.wr(MULq(DEST_XYZW, 1, 0))
        block.wr(lower=func(17))
        _mulq_run(block, 2, 14)
    elif variant.startswith('stalled ('):
        _fdiv_prologue(block)
        block.wr(lower=func(16))
        block.wr(MADD(DEST_XYZW, 30, 23, 0))
        for _ in range(int(variant[9:-1])):
            block.wr(MULq(DEST_XYZW, 0, 0))
        block.wr(CLIP(30, 30))
        _mulq_run(block, 1, 14)
    elif variant == 'stalled with branch':
        _fdiv_prologue(block)
        label = block.wr(lower=func(16))
        here = len(block.pairs)
        block.wr(MADD(DEST_XYZW, 30, 23, 0), IBLTZ(0, label - (here + 1)))  # never taken: VI00 is 0
        block.wr()  # delay slot
        block.wr(CLIP(30, 30))
        _mulq_run(block, 1, 14)
    else:
        raise ValueError(variant)
    return block


def branch_program(target, ret, result):
    block = Block()
    block.wr(lower=IADDI(result, 0, 0))
    block.wr(lower=IADDI(target, 0, 6))
    block.wr(lower=JALR(ret, target))
    block.wr()  # delay slot
    block.wr(lower=B(2))  # to the exit label, past the target pair
    block.wr()  # delay slot
    block.wr(lower=IADDI(result, 0, 1))  # pair 6
    block.wr()  # exit label
    return block


def cases():
    """(label, unit, block, kind, expectation)."""
    fixture = json.loads(FIXTURE.read_text())
    inputs = {k: [int(x, 16) for x in v] for k, v in fixture['efu_inputs'].items()}
    out = []
    for item in fixture['efu']:
        out.append((f"EFU {item['op']} {item['input']}", 'vu1', efu_program(item['op']), 'efu',
                    dict(op=item['op'], vf1=inputs[item['input']], p=int(item['expected'], 16))))
    for section, regs in fixture['fdivdelay'].items():
        name, variant = section.split(' ', 1)
        out.append((f'FDIV {section}', 'vu0', fdiv_program(name, variant), 'fdiv',
                    {int(r): [int(x, 16) for x in v.split()] for r, v in regs.items()}))
    for item, regs in zip(fixture['branch'], ((1, 2, 3), (1, 1, 3))):
        out.append((item['name'], 'vu0', branch_program(*regs), 'branch',
                    dict(regs=regs, target=item['target'], ret=item['ret'], result=item['result'])))
    return out


def check(work=None):
    import pipeline as lab
    case_list = cases()
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='vu-hardware-check-')) if owned else Path(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        for index, (_, _, block, _, _) in enumerate(case_list):
            code = block.code()
            for unit, size in (('vu0', 4096), ('vu1', 16384)):
                (work / f'{unit}_{index}.bin').write_bytes(code.ljust(size, b'\0'))
        harness = vm.build(work)
        inputs = bytearray()
        for index, (_, unit, _, kind, expect) in enumerate(case_list):
            state = bc.State([[0, 0] for _ in range(32)], 0, 0, 0, 0, b'')
            state.vf = [[0, 0, 0, bc.ONE]] + [[0] * 4 for _ in range(31)]
            state.vacc, state.q, state.i, state.clip, state.mac, state.status = [0] * 4, 0, 0, 0, 0, 0
            state.vi = [0] * 16
            state.vumem = bytearray(vm.UNITS[unit]['data'])
            state.p, state.r, state.top, state.itop, state.vumask = 0, 0x3F800000, 0, 0, vm.UNITS[unit]['mask']
            if kind == 'efu':
                state.vf[1] = list(expect['vf1'])
            inputs += vm.pack(unit, index, 0, state)
        ran = subprocess.run([str(harness), str(work), str(len(case_list))], input=bytes(inputs), capture_output=True,
                             timeout=600)
        if ran.returncode:
            raise RuntimeError(f'VU harness failed ({ran.returncode}): {ran.stderr.decode(errors="replace")[-2000:]}')
        size = struct.calcsize(vm.OUTPUT)
        results = []
        for index, (label, unit, _, kind, expect) in enumerate(case_list):
            words = struct.unpack_from(f'<{vm.WORDS}I', ran.stdout, index * size)
            vf = [list(words[4 * r:4 * r + 4]) for r in range(32)]
            vi = list(words[128:144])
            if kind == 'efu':
                got, want = vf[2][0], expect['p']
                if got == want:
                    status = 'exact'
                elif expect['op'] not in EXACT_EFU and (got ^ want) >> 31 == 0 and abs(got - want) <= 8:
                    status = 'near'
                else:
                    status = 'wrong'
                results.append(dict(case=label, status=status, got=f'{got:08x}', expected=f'{want:08x}'))
            elif kind == 'fdiv':
                bad = {r: (' '.join(f'{x:08x}' for x in vf[r]), ' '.join(f'{x:08x}' for x in want))
                       for r, want in expect.items() if vf[r] != want}
                results.append(dict(case=label, status='exact' if not bad else 'wrong', differences=bad))
            else:
                t, r, res = expect['regs']
                got = dict(target=vi[t], ret=vi[r], result=vi[res])
                want = dict(target=expect['target'], ret=expect['ret'], result=expect['result'])
                results.append(dict(case=label, status='exact' if got == want else 'wrong', got=got, expected=want))
        required_wrong = [r for r in results if r['status'] == 'wrong' and not (
            r['case'].startswith('EFU') and r['case'].split()[1] not in EXACT_EFU)]
        return dict(schema=1, cases=len(results), exact=sum(r['status'] == 'exact' for r in results),
                    near=sum(r['status'] == 'near' for r in results),
                    wrong=sum(r['status'] == 'wrong' for r in results),
                    passed=not required_wrong, failures=required_wrong,
                    approximate=[r for r in results if r['status'] != 'exact' and r not in required_wrong])
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--work')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    report = check(args.work)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + '\n')
    print(f"{report['cases']} hardware cases: {report['exact']} exact, {report['near']} within last-bit steps, "
          f"{report['wrong']} wrong; required cases {'all match' if report['passed'] else 'FAIL'}")
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
