"""VU microprogram behaviour check: the runtime's VU interpreter against an independent model.

Each instruction form becomes a tiny microprogram (the instruction pair, NOP pairs so every pipeline
drains, then an E-bit pair and its delay slot) placed in its own 128-byte slot of a VU0 (4 KiB) or VU1
(16 KiB) code image. The runtime's VU source is compiled with a small harness and runs each program
from its slot on random register, flag and data-memory states; the final state is compared with the
model. Upper instructions and the lower forms shared with macro mode reuse behaviour_check.py's VU0
model (encodings are the macro encodings without the COP2 prefix); micro-only lower forms are modelled
here from the VU instruction set (field layouts and flag rules as in PCSX2 VUops.cpp).

EFU results that ps2float models bit-exactly (ERCPR, ESQRT, ERSQRT) are compared exactly; the other
EFU functions have no bit-exact reference and are compared within a relative tolerance, reported
separately. Not covered: XGKICK (GS transfer) and pipeline timing; see TIMING_CASES for the ordered
cases that are.
"""
import json
import math
import platform
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import behaviour_check as bc

M32 = bc.M32
UNITS = {'vu0': dict(code=4096, data=4096, mask=0xFF, unit=0), 'vu1': dict(code=16384, data=16384, mask=0x3FF, unit=1)}
# VU0 has no EFU (so no P register, MFP or WAITP) and no XGKICK/XTOP; those forms run on VU1 only.
SLOT = 128  # bytes per case
NOP_LOWER, NOP_UPPER = 0x8000033C, 0x000002FF
E_BIT, I_BIT = 1 << 30, 1 << 31
DRAIN_PAIRS = 6

# Forms shared with macro mode. Lower-unit ones carry the lower prefix; the rest are upper.
MACRO_LOWER = {'vdiv', 'vsqrt', 'vrsqrt', 'vmove', 'vmr32', 'vmtir', 'vmfir', 'viadd', 'visub', 'viaddi', 'viand',
               'vior', 'vlqi', 'vsqi', 'vlqd', 'vsqd', 'vilwr', 'viswr'}
EE_ONLY = {'qmfc2', 'qmtc2', 'cfc2', 'ctc2', 'lqc2', 'sqc2', 'vnop', 'vwaitq'}
WAITQ_LOWER = 0x8000003C | (0x3B >> 2 << 6) | (0x3B & 3)
WAITP_LOWER = 0x8000003C | (0x7B >> 2 << 6) | (0x7B & 3)


def _lower_special(opcode, f):
    return (0x80000000 | (f.get('dest', 0) << 21) | (f.get('vt', 0) << 16) | (f.get('vs', 0) << 11)
            | ((opcode >> 2) << 6) | 0x3C | (opcode & 3))


def _lower_direct(op, f, imm11=None):
    imm = f.get('imm', 0) if imm11 is None else imm11
    return (op << 25) | (f.get('dest', 0) << 21) | (f.get('vt', 0) << 16) | (f.get('vs', 0) << 11) | (imm & 0x7FF)


# ---- micro-only model ------------------------------------------------------------------------------
def _imm11(f):
    return bc.sext(f['imm'] & 0x7FF, 11)


def _vu_qword(s, base_reg, f):
    return ((bc._vi(s, base_reg) + _imm11(f)) & s.vumask) << 4


def _load_words(s, address):
    return [bc._vu_load(s, address + 4 * k) for k in range(4)]


def _lq(s, f):
    bc._vf_write(s, f['vt'], _load_words(s, _vu_qword(s, f['vs'], f)), f['dest'])


def _sq(s, f):
    address, v = _vu_qword(s, f['vt'], f), bc._vf(s, f['vs'])
    for k in range(4):
        if f['dest'] & (8 >> k):
            bc._vu_store(s, address + 4 * k, v[k])


def _ilw(s, f):
    bc._vi_write(s, f['vt'], _load_words(s, _vu_qword(s, f['vs'], f))[bc._lane_of(f['dest'])])


def _isw(s, f):
    address = _vu_qword(s, f['vs'], f)
    for k in range(4):
        if f['dest'] & (8 >> k):
            bc._vu_store(s, address + 4 * k, bc._vi(s, f['vt']))


def _imm15(f):
    return ((f['dest'] & 0xF) << 11) | (f['imm'] & 0x7FF)


def _imm12(f):
    return ((f['dest'] & 1) << 11) | (f['imm'] & 0x7FF)


def _imm24(f):
    return f['imm24'] & 0xFFFFFF


MICRO_FORMS = {
    'lq': (lambda f: _lower_direct(0x00, f), _lq),
    'sq': (lambda f: _lower_direct(0x01, f), _sq),
    'ilw': (lambda f: _lower_direct(0x04, f), _ilw),
    'isw': (lambda f: _lower_direct(0x05, f), _isw),
    'iaddiu': (lambda f: _lower_direct(0x08, f), lambda s, f: bc._vi_write(s, f['vt'], bc._vi(s, f['vs']) + _imm15(f))),
    'isubiu': (lambda f: _lower_direct(0x09, f), lambda s, f: bc._vi_write(s, f['vt'], bc._vi(s, f['vs']) - _imm15(f))),
    'fceq': (lambda f: (0x10 << 25) | _imm24(f), lambda s, f: bc._vi_write(s, 1, int(s.clip & 0xFFFFFF == _imm24(f)))),
    'fcset': (lambda f: (0x11 << 25) | _imm24(f), lambda s, f: setattr(s, 'clip', _imm24(f))),
    'fcand': (lambda f: (0x12 << 25) | _imm24(f), lambda s, f: bc._vi_write(s, 1, int(bool(s.clip & _imm24(f))))),
    'fcor': (lambda f: (0x13 << 25) | _imm24(f),
             lambda s, f: bc._vi_write(s, 1, int((s.clip | _imm24(f)) & 0xFFFFFF == 0xFFFFFF))),
    'fseq': (lambda f: _lower_direct(0x14, f), lambda s, f: bc._vi_write(s, f['vt'], int(s.status & 0xFFF == _imm12(f)))),
    'fsset': (lambda f: _lower_direct(0x15, f), lambda s, f: setattr(s, 'status', (_imm12(f) & 0xFC0) | (s.status & 0x3F))),
    'fsand': (lambda f: _lower_direct(0x16, f), lambda s, f: bc._vi_write(s, f['vt'], s.status & 0xFFF & _imm12(f))),
    'fsor': (lambda f: _lower_direct(0x17, f), lambda s, f: bc._vi_write(s, f['vt'], (s.status & 0xFFF) | _imm12(f))),
    'fmeq': (lambda f: _lower_direct(0x18, f), lambda s, f: bc._vi_write(s, f['vt'], int(s.mac & 0xFFFF == bc._vi(s, f['vs'])))),
    'fmand': (lambda f: _lower_direct(0x1A, f), lambda s, f: bc._vi_write(s, f['vt'], s.mac & 0xFFFF & bc._vi(s, f['vs']))),
    'fmor': (lambda f: _lower_direct(0x1B, f), lambda s, f: bc._vi_write(s, f['vt'], (s.mac & 0xFFFF) | bc._vi(s, f['vs']))),
    'fcget': (lambda f: _lower_direct(0x1C, f), lambda s, f: bc._vi_write(s, f['vt'], s.clip & 0xFFF)),
    'mfp': (lambda f: _lower_special(0x64, f), lambda s, f: bc._vf_write(s, f['vt'], [s.p] * 4, f['dest'])),
    'xtop': (lambda f: _lower_special(0x68, f), lambda s, f: bc._vi_write(s, f['vt'], s.top)),
    'xitop': (lambda f: _lower_special(0x69, f), lambda s, f: bc._vi_write(s, f['vt'], s.itop)),
    'rget': (lambda f: _lower_special(0x41, f), lambda s, f: bc._vf_write(s, f['vt'], [s.r] * 4, f['dest'])),
    'rinit': (lambda f: _lower_special(0x42, f),
              lambda s, f: setattr(s, 'r', 0x3F800000 | (bc._vf(s, f['vs'])[bc._field(f, 'fsf')] & 0x7FFFFF))),
    'rxor': (lambda f: _lower_special(0x43, f),
             lambda s, f: setattr(s, 'r', 0x3F800000 | ((s.r ^ bc._vf(s, f['vs'])[bc._field(f, 'fsf')]) & 0x7FFFFF))),
}


def _rnext(s, f):
    x, y = (s.r >> 4) & 1, (s.r >> 22) & 1
    s.r = 0x3F800000 | ((((s.r << 1) ^ x ^ y)) & 0x7FFFFF)
    bc._vf_write(s, f['vt'], [s.r] * 4, f['dest'])


MICRO_FORMS['rnext'] = (lambda f: _lower_special(0x40, f), _rnext)

# EFU: P = f(vs). Exact where ps2float has the function; otherwise a float reference within tolerance.
def _fsf_value(s, f):
    return bc._vf(s, f['vs'])[bc._field(f, 'fsf')]


def _xyz(s, f):
    return bc._vf(s, f['vs'])[:3]


def _efu_exact(name):
    return lambda s, f: setattr(s, 'p', bc._pf(name, _fsf_value(s, f)))


def _efu_float(fn):
    def run(s, f):
        s.p = bc._nearest(fn(s, f))
    return run


def _sum_squares(s, f):
    return sum(bc._value(x) ** 2 for x in _xyz(s, f))


EFU_FORMS = {
    'ercpr': (0x7A, _efu_exact('ercpr'), True), 'esqrt': (0x78, _efu_exact('esqrt'), True),
    'ersqrt': (0x79, _efu_exact('ersqrt'), True),
    'esadd': (0x70, _efu_float(_sum_squares), False),
    'ersadd': (0x71, _efu_float(lambda s, f: 1 / _sum_squares(s, f) if _sum_squares(s, f) else 0.0), False),
    'eleng': (0x72, _efu_float(lambda s, f: math.sqrt(_sum_squares(s, f))), False),
    'erleng': (0x73, _efu_float(lambda s, f: 1 / math.sqrt(_sum_squares(s, f)) if _sum_squares(s, f) else 0.0), False),
    'esum': (0x76, _efu_float(lambda s, f: sum(bc._value(x) for x in bc._vf(s, f['vs']))), False),
    'esin': (0x7C, _efu_float(lambda s, f: math.sin(bc._value(_fsf_value(s, f)))), False),
    'eatan': (0x7D, _efu_float(lambda s, f: math.atan(bc._value(_fsf_value(s, f)))), False),
    'eexp': (0x7E, _efu_float(lambda s, f: math.exp(-bc._value(_fsf_value(s, f)))), False),
}
for _name, (_op, _run, _) in EFU_FORMS.items():
    MICRO_FORMS[_name] = ((lambda op: lambda f: _lower_special(op, f))(_op), _run)

# Branches: `branch; iaddiu vi8 += 1 (delay); iaddiu vi9 += 1 (skipped when taken); target: ...`.
BRANCH_FORMS = {
    'b': (0x20, lambda a, b: True), 'bal': (0x21, lambda a, b: True),
    'ibeq': (0x28, lambda a, b: a == b), 'ibne': (0x29, lambda a, b: a != b),
    'ibltz': (0x2C, lambda a, b: bc.sext(a, 16) < 0), 'ibgtz': (0x2D, lambda a, b: bc.sext(a, 16) > 0),
    'iblez': (0x2E, lambda a, b: bc.sext(a, 16) <= 0), 'ibgez': (0x2F, lambda a, b: bc.sext(a, 16) >= 0),
}
TAKEN_VI, SKIPPED_VI, LINK_VI = 8, 9, 10
VU1_ONLY = {'mfp', 'xtop'}


def _iaddiu(reg, amount=1):
    return (0x08 << 25) | (reg << 16) | (reg << 11) | amount


def _branch_words(name, f):
    op, _ = BRANCH_FORMS[name]
    it = LINK_VI if name == 'bal' else f['vt']
    branch = (op << 25) | (it << 16) | (f['vs'] << 11) | 2  # target: two pairs after the delay slot
    return [(branch, NOP_UPPER), (_iaddiu(TAKEN_VI), NOP_UPPER), (_iaddiu(SKIPPED_VI), NOP_UPPER)]


def _branch_run(name):
    def run(s, f):
        _, condition = BRANCH_FORMS[name]
        taken = condition(bc._vi(s, f['vs']), bc._vi(s, f['vt']))
        if name == 'bal':
            bc._vi_write(s, LINK_VI, (f['start'] + 16) // 8)
        bc._vi_write(s, TAKEN_VI, bc._vi(s, TAKEN_VI) + 1)
        if not taken:
            bc._vi_write(s, SKIPPED_VI, bc._vi(s, SKIPPED_VI) + 1)
    return run


# ---- cases -----------------------------------------------------------------------------------------
def cases(rng, variants=3):
    """(name, [(lower, upper)] head pairs, fields, wait lower or None)."""
    out = []
    for name, form in bc.VU_FORMS.items():
        if name in EE_ONLY:
            continue
        for v in range(variants):
            fields = _fields(rng, name, v)
            word = form['encode'](fields) & 0x01FFFFFF
            if name in MACRO_LOWER:
                pair = (0x80000000 | word, NOP_UPPER)
                wait = WAITQ_LOWER if name in ('vdiv', 'vsqrt', 'vrsqrt') else None
            else:
                pair = (NOP_LOWER, word)
                wait = None
            out.append((name, [pair], fields, wait))
    for name, (encode, _) in MICRO_FORMS.items():
        for v in range(variants):
            fields = _fields(rng, name, v)
            wait = WAITP_LOWER if name in EFU_FORMS else None
            out.append((name, [(encode(fields), NOP_UPPER)], fields, wait))
    for name in BRANCH_FORMS:
        for v in range(variants):
            fields = _fields(rng, name, v)
            fields['vs'], fields['vt'] = rng.sample([r for r in range(1, 16) if r not in (TAKEN_VI, SKIPPED_VI, LINK_VI)], 2)
            if v == 1:
                fields['vt'] = fields['vs']
            out.append((name, _branch_words(name, fields), fields, None))
    for v in range(variants):  # LOI: an upper instruction with the I bit loads the lower word into I
        fields = dict(_fields(rng, 'loi', v), imm=rng.getrandbits(32))
        out.append(('loi', [(fields['imm'], NOP_UPPER | I_BIT)], fields, None))
    return out


def _fields(rng, name, v):
    vs, vt, vd = rng.sample(range(1, 32), 3)
    fields = dict(vs=vs, vt=vt, vd=vd, dest=rng.randrange(1, 16), imm=rng.randrange(1 << 11), imm24=rng.getrandbits(24),
                  rs=0, rt=0, rd=0, sa=0)
    if v == 1:
        fields.update(vt=vs, vd=vs)
    if v == 2:
        fields.update(vt=0, vd=0)
    if name in ('vopmula', 'vopmsub', 'vclipw'):
        fields['dest'] = 0xE
    if name in ('vilwr', 'viswr', 'ilw'):
        fields['dest'] = rng.choice([8, 4, 2, 1])
    integer = name in ('viadd', 'visub', 'viand', 'vior', 'viaddi', 'vmtir', 'vmfir', 'vlqi', 'vsqi', 'vlqd', 'vsqd',
                       'vilwr', 'viswr', 'lq', 'sq', 'ilw', 'isw', 'iaddiu', 'isubiu', 'fseq', 'fsset', 'fsand', 'fsor',
                       'fmeq', 'fmand', 'fmor', 'fcget', 'xtop', 'xitop')
    if integer:
        fields.update(vs=fields['vs'] & 15, vt=fields['vt'] & 15)
        if name not in ('viaddi',):
            fields['vd'] &= 15
    return fields


def _model(name):
    if name in BRANCH_FORMS:
        return _branch_run(name)
    if name == 'loi':
        return lambda s, f: setattr(s, 'i', f['imm'])
    if name in MICRO_FORMS:
        return MICRO_FORMS[name][1]
    return bc.VU_FORMS[name]['run']


def random_state(rng, name, fields, unit):
    s = bc.random_state(rng, 'vadd' if name not in bc.VU_FORMS else name, dict(fields, rs=0, rt=0, rd=0))
    s.vumem = bytearray(rng.getrandbits(8) for _ in range(UNITS[unit]['data']))
    s.vumask = UNITS[unit]['mask']
    s.p, s.r = bc.random_float(rng), 0x3F800000 | rng.getrandbits(23)
    s.top, s.itop = rng.getrandbits(10), rng.getrandbits(10)
    s.vi[TAKEN_VI] = s.vi[SKIPPED_VI] = 0
    s.vf[0] = [0, 0, 0, bc.ONE]
    if name in BRANCH_FORMS and rng.random() < 0.4:
        s.vi[fields['vt']] = s.vi[fields['vs']]  # equal operands
    if name in ('lq', 'sq', 'ilw', 'isw') and fields['vs'] and rng.random() < 0.5:  # VI0 stays 0
        s.vi[fields['vs']] = rng.randrange(-8, 8) & 0xFFFF  # near the start of data memory, with wrap
    if name in ('ersqrt', 'esqrt') and rng.random() < 0.5:
        lane = bc._field(fields, 'fsf')
        s.vf[fields['vs']][lane] &= 0x7FFFFFFF  # non-negative operands as well
    if name in ('esin', 'eatan', 'eexp', 'esum', 'esadd', 'ersadd', 'eleng', 'erleng'):
        for k in range(4):  # the polynomial units are specified for moderate operands
            s.vf[fields['vs']][k] = (rng.getrandbits(1) << 31) | (rng.randrange(0x70, 0x80) << 23) | rng.getrandbits(23)
        if name in ('eatan', 'eatanxy', 'eatanxz', 'eexp'):
            for k in range(4):  # EATAN's series is for x >= 0 (|t| <= 1); EEXP computes e^-x for x >= 0
                s.vf[fields['vs']][k] &= 0x7FFFFFFF
    return s


def expected(name, fields, state):
    out = state.copy()
    out.vumask, out.p, out.r, out.top, out.itop = state.vumask, state.p, state.r, state.top, state.itop
    _model(name)(out, fields)
    return out


# ---- harness ---------------------------------------------------------------------------------------
# Record: unit, start pc, then VF (128 words), VI (16), ACC (4), Q P I R MAC CLIP STATUS TOP ITOP, data.
WORDS = 128 + 16 + 4 + 9
DATA_MAX = 16384
RECORD = f'<3I{WORDS}I{DATA_MAX}s'
OUTPUT = f'<{WORDS}I{DATA_MAX}s'

HARNESS = r'''
#include "runtime/ps2_vu1.h"
#include "runtime/ps2_memory.h"
#include "runtime/gs/gs_frontend.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
// GS is reached only by XGKICK, which no case uses.
alignas(64) static unsigned char g_gs[sizeof(GS)];
#ifdef VU_MICRO_COMPILED
// Compiled programs (tools/vu1_recomp.cpp output) are looked up through PS2Memory's code generation,
// which reads 0 from this zeroed stand-in.
void registerGeneratedVu1Programs();
alignas(64) static unsigned char g_memory[sizeof(PS2Memory)];
#endif
int main(int argc, char **argv) {
    // argv[1]: directory of vu0_<batch>.bin / vu1_<batch>.bin, argv[2]: batch count
    const int batches = std::atoi(argv[2]);
    std::vector<std::vector<uint8_t>> images(2 * batches);
    for (int b = 0; b < batches; ++b)
        for (int u = 0; u < 2; ++u) {
            const std::string path = std::string(argv[1]) + "/vu" + std::to_string(u) + "_" + std::to_string(b) + ".bin";
            FILE *f = std::fopen(path.c_str(), "rb");
            auto &image = images[2 * b + u];
            image.resize(u ? 16384 : 4096);
            if (!f || std::fread(image.data(), 1, image.size(), f) != image.size()) return 2;
            std::fclose(f);
        }
    static uint8_t data[16384];
    uint32_t head[3], w[@WORDS@];
    PS2Memory *memory = nullptr;
#ifdef VU_MICRO_COMPILED
    registerGeneratedVu1Programs();
    memory = reinterpret_cast<PS2Memory *>(g_memory);
#endif
    while (std::fread(head, 4, 3, stdin) == 3) {
        if (std::fread(w, 4, @WORDS@, stdin) != @WORDS@ || std::fread(data, 1, sizeof(data), stdin) != sizeof(data)) return 3;
        VU1Interpreter vu(head[0] ? VU1Interpreter::Unit::VU1 : VU1Interpreter::Unit::VU0);
        vu.reset();
        VU1State &s = vu.state();
        std::memcpy(s.vf, w, 512);
        for (int k = 0; k < 16; ++k) s.vi[k] = (int32_t)(int16_t)w[128 + k];
        std::memcpy(s.acc, w + 144, 16);
        std::memcpy(&s.q, w + 148, 4); std::memcpy(&s.p, w + 149, 4); std::memcpy(&s.i, w + 150, 4);
        s.r = w[151]; s.mac = w[152]; s.clip = w[153]; s.status = w[154];
        const uint32_t dataSize = head[0] ? 16384u : 4096u;
        auto &image = images[2 * head[1] + head[0]];
        vu.execute(image.data(), (uint32_t)image.size(), data, dataSize,
                   *reinterpret_cast<GS *>(g_gs), memory, head[2], w[155], w[156], 100000);
        std::memcpy(w, s.vf, 512);
        for (int k = 0; k < 16; ++k) w[128 + k] = (uint32_t)s.vi[k] & 0xFFFFu;
        std::memcpy(w + 144, s.acc, 16);
        std::memcpy(w + 148, &s.q, 4); std::memcpy(w + 149, &s.p, 4); std::memcpy(w + 150, &s.i, 4);
        w[151] = s.r; w[152] = s.mac; w[153] = s.clip; w[154] = s.status;
        std::fwrite(w, 4, @WORDS@, stdout);
        std::fwrite(data, 1, sizeof(data), stdout);
    }
    return 0;
}
'''


def pack(unit, batch, pc, s):
    vf = [w for r in s.vf for w in r]
    vf[0:4] = [0, 0, 0, bc.ONE]
    words = vf + [x & 0xFFFF for x in s.vi] + s.vacc + [s.q, s.p, s.i, s.r, s.mac, s.clip, s.status, s.top, s.itop]
    return struct.pack(RECORD, UNITS[unit]['unit'], batch, pc, *words, bytes(s.vumem).ljust(DATA_MAX, b'\0'))


def unpack(raw, like, unit):
    values = struct.unpack(OUTPUT, raw)
    out = like.copy()
    out.vumask, out.top, out.itop = like.vumask, like.top, like.itop
    out.vf = [list(values[4 * r:4 * r + 4]) for r in range(32)]
    out.vi = list(values[128:144])
    out.vacc = list(values[144:148])
    out.q, out.p, out.i, out.r, out.mac, out.clip, out.status = values[148:155]
    out.vumem = bytearray(values[-1][:UNITS[unit]['data']])
    return out


def describe(want, got):
    diffs = bc.describe(want, got)
    for key in ('p', 'r'):
        if getattr(want, key) != getattr(got, key):
            diffs.append(f'{key} {getattr(got, key):#010x} expected {getattr(want, key):#010x}')
    return diffs


BATCH = UNITS['vu0']['code'] // SLOT  # cases per image pair (VU0 holds 32)


def images(case_list):
    """Code images in batches; each batch has a VU0 and a VU1 image with case k of the batch in slot k.
    Returns {(unit, batch): image} and each case's (batch, start pc)."""
    code, placed = {}, []
    for index, (name, head, fields, wait) in enumerate(case_list):
        batch, pc = divmod(index, BATCH)
        pc *= SLOT
        pairs = list(head) + [((wait if (k == 0 and wait) else NOP_LOWER), NOP_UPPER) for k in range(DRAIN_PAIRS)]
        pairs += [(NOP_LOWER, NOP_UPPER | E_BIT), (NOP_LOWER, NOP_UPPER)]
        assert 8 * len(pairs) <= SLOT
        for u in UNITS:
            image = code.setdefault((u, batch), bytearray(UNITS[u]['code']))
            for k, (lower, upper) in enumerate(pairs):
                struct.pack_into('<2I', image, pc + 8 * k, lower, upper)
        placed.append((batch, pc))
    return code, placed


def _flags(work):
    import pipeline as lab
    runtime = lab.SOURCE / 'ps2xRuntime'
    flags = [part for part in lab.syntax_check_command(work) if part not in ('-fsyntax-only', '-ferror-limit=8')]
    return flags + ['-O1', '-w', '-I', str(runtime / 'src/lib/vu')], runtime


def compile_programs(work, images):
    """Code images ({(unit, batch): image}) through the engine's vector compiler (tools/vu1_recomp.cpp,
    vu1_*.bin and vu0_*.bin); returns the C++ sources."""
    flags, runtime = _flags(work)
    tool = work / 'vu1_recomp'
    built = subprocess.run(flags + [str(Path(__file__).resolve().parent / 'tools/vu1_recomp.cpp'),
                                    str(runtime / 'src/lib/vu/ps2_vu1_core.cpp'), str(runtime / 'src/lib/vu/ps2float.cpp'),
                                    '-Wl,-undefined,dynamic_lookup' if platform.system() == 'Darwin' else '-Wl,--unresolved-symbols=ignore-all',
                                    '-o', str(tool)], capture_output=True, text=True, timeout=900)
    if built.returncode:
        raise RuntimeError('Vector compiler did not build:\n' + built.stderr[-3000:])
    capture, generated = work / 'capture', work / 'compiled'
    capture.mkdir(exist_ok=True)
    generated.mkdir(exist_ok=True)
    for (unit, batch), image in images.items():
        (capture / f'{unit}_{batch:04d}.bin').write_bytes(bytes(image))
    ran = subprocess.run([str(tool), str(capture), str(generated)], capture_output=True, text=True, timeout=900)
    if ran.returncode:
        raise RuntimeError('Vector compiler failed:\n' + (ran.stdout + ran.stderr)[-3000:])
    return sorted(generated.glob('*.cpp'))


def build(work, compiled_sources=()):
    flags, runtime = _flags(work)
    (work / 'harness.cpp').write_text(HARNESS.replace('@WORDS@', str(WORDS)))
    command = flags + ['-I', str(runtime / 'src/lib/vu'), str(work / 'harness.cpp'),
                       str(runtime / 'src/lib/vu/ps2_vu1_core.cpp'), str(runtime / 'src/lib/vu/ps2float.cpp')]
    if compiled_sources:
        command += ['-DVU_MICRO_COMPILED'] + [str(p) for p in compiled_sources]
    command += ['-Wl,-undefined,dynamic_lookup'] if platform.system() == 'Darwin' else ['-Wl,--unresolved-symbols=ignore-all']
    command += ['-o', str(work / 'vu-harness')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if built.returncode:
        raise RuntimeError('VU harness did not compile:\n' + built.stderr[-4000:])
    return work / 'vu-harness'


def tolerance_match(name, want, got):
    if name not in EFU_FORMS or EFU_FORMS[name][2]:
        return False
    rest_want, rest_got = want.copy(), got.copy()
    rest_want.p = rest_got.p = 0
    if describe(rest_want, rest_got):
        return False
    a, b = bc._value(want.p), bc._value(got.p)
    # Relative 1e-5, plus an absolute 2.5e-7 for EATAN near zero, where the unit's result is pi/4
    # minus a series near pi/4 (the hardware itself returns 1.07e-7 for EATAN(0)).
    return abs(a - b) <= 1e-5 * max(abs(a), abs(b)) + 2.5e-7


def check(states=48, seed=1, variants=3, work=None, only=None, units=('vu0', 'vu1'), compiled=False):
    """compiled: run the cases through C++ compiled from their code images (tools/vu1_recomp.cpp)."""
    rng = random.Random(seed)
    case_list = cases(rng, variants)
    if only:
        case_list = [c for c in case_list if c[0] in only]
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='vu-micro-check-')) if owned else Path(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        code, placed = images(case_list)
        batches = 1 + max(b for b, _ in placed)
        for (u, batch), image in code.items():
            (work / f'{u}_{batch}.bin').write_bytes(bytes(image))
        sources = compile_programs(work, code) if compiled else ()
        harness = build(work, sources)
        inputs, expectations = bytearray(), []
        for index, (name, head, fields, _) in enumerate(case_list):
            batch, start = placed[index]
            fields['start'] = start
            for unit in units:
                if unit == 'vu0' and (name in EFU_FORMS or name in VU1_ONLY):
                    continue
                made = tries = 0
                while made < states:
                    tries += 1
                    if tries > 100 * states:
                        raise RuntimeError(f'{name}: the input generator rarely produces inputs inside the check')
                    state = random_state(rng, name, fields, unit)
                    try:
                        after = expected(name, fields, state)
                    except bc.Trap:
                        continue
                    inputs += pack(unit, batch, start, state)
                    expectations.append((index, unit, state, after))
                    made += 1
        ran = subprocess.run([str(harness), str(work), str(batches)], input=bytes(inputs),
                             capture_output=True, timeout=1800)
        if ran.returncode:
            raise RuntimeError(f'VU harness failed ({ran.returncode}): {ran.stderr.decode(errors="replace")[-2000:]}')
        size = struct.calcsize(OUTPUT)
        if len(ran.stdout) != size * len(expectations):
            raise RuntimeError('VU harness output is truncated')
        forms = {}
        for k, (index, unit, before, after) in enumerate(expectations):
            name, head, fields, _ = case_list[index]
            actual = unpack(ran.stdout[k * size:(k + 1) * size], after, unit)
            key = f'{name}@{unit}'
            record = forms.setdefault(key, dict(cases=0, failures=0, rounding=0, approximate=0, examples=[]))
            record['cases'] += 1
            diffs = describe(after, actual)
            if not diffs:
                continue
            if bc.rounding_difference(name, fields, before, after, actual) if name in bc.VU_FORMS else False:
                record['rounding'] += 1
            elif tolerance_match(name, after, actual):
                record['approximate'] += 1
            else:
                record['failures'] += 1
                if len(record['examples']) < 3:
                    record['examples'].append(dict(words=[f'{l:#010x}/{u:#010x}' for l, u in head],
                                                   fields={x: fields[x] for x in ('vs', 'vt', 'vd', 'dest', 'imm') if x in fields},
                                                   inputs=dict(vs=[f'{x:08x}' for x in bc._vf(before, fields.get('vs', 0))],
                                                               vt=[f'{x:08x}' for x in bc._vf(before, fields.get('vt', 0))],
                                                               acc=[f'{x:08x}' for x in before.vacc],
                                                               q=f'{before.q:08x}', i=f'{before.i:08x}'),
                                                   differences=diffs[:6]))
        failing = sorted(n for n, r in forms.items() if r['failures'])
        return dict(schema=1, seed=seed, states_per_case=states, forms=len(forms), executions=len(expectations),
                    failing_forms=failing, rounding_forms=sorted(n for n, r in forms.items() if r['rounding']),
                    approximate_forms=sorted(n for n, r in forms.items() if r['approximate']),
                    passed=not failing, results=forms)
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--states', type=int, default=48)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--units', nargs='*', default=['vu0', 'vu1'])
    parser.add_argument('--work')
    parser.add_argument('--out')
    parser.add_argument('--compiled', action='store_true', help='run the cases through compiled C++ programs')
    args = parser.parse_args(argv)
    report = check(args.states, args.seed, work=args.work, only=args.only, units=tuple(args.units), compiled=args.compiled)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + '\n')
    print(f"{report['forms']} forms, {report['executions']} executions: "
          + ('all match the reference model' if report['passed'] else 'mismatches in ' + ', '.join(report['failing_forms'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
