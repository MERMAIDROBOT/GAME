"""Instruction behaviour check: the real translator against an independent model of the EE.

Every instruction form is wrapped as a tiny guest function (the instruction, then `jr ra`), all
of them in one synthetic executable that the pinned recompiler translates like a game. The
generated C++ is compiled against the real runtime headers and run on random register and memory
states; the result is compared with a reference model written from the R5900 instruction set
(below), not from the translator. No game, disc or retail code is involved.

Scope: integer ALU, shifts, HI/LO and pipeline-1 multiply/divide, loads and stores (including the
unaligned left/right forms and 128-bit LQ/SQ), conditional branches with their delay slots, traps
that do not fire, the 128-bit multimedia (MMI) forms (QFSRV run after MTSAB/MTSAH), the FPU (COP1)
and VU0 macro mode (COP2) with its MAC zero/sign and clip flags. Float arithmetic is checked against
tools/reference/ps2float.c; differences of the last bit (two for forms that round twice, judged
against the same model with host-rounded products) are the engine's accepted host-rounding policy
and are reported separately. Not covered: calls and link branches (they need the runtime's function
table), VU0 microprograms, VU memory forms, and the FPU/VU overflow/underflow flag bits.
Inputs that would raise a CPU exception (integer overflow traps, misaligned natural loads) are
not generated; division by zero and INT_MIN / -1 are, because the EE defines their results.
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

M64 = (1 << 64) - 1
M32 = (1 << 32) - 1
CODE_BASE = 0x00100000
DATA_BASE = 0x00200000
DATA_BYTES = 256
RA = 31
VU_WORDS = 32 * 4 + 4 + 5 + 16
VU0_DATA_BYTES = 4096
BRANCH_TAKEN_REG, BRANCH_SKIPPED_REG = 8, 9  # counters the branch programs increment
NOP, JR_RA = 0, 0x03E00008


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def s32(value):
    return sext(value, 32) & M64


def signed64(value):
    return sext(value, 64)


class State:
    """Lower and upper 64 bits of each GPR, HI/LO of both pipelines, the data window, and the FPU:
    32 single registers and the accumulator as raw bits, and FCR31; and VU0 in macro mode: 32 vector
    registers and ACC (x, y, z, w as raw bits), Q, I, the clip flags and VI0-VI15."""
    def __init__(self, gpr, hi, lo, hi1, lo1, mem, fpr=None, acc=0, fcr31=0, vu=None, vumem=None):
        self.gpr, self.hi, self.lo, self.hi1, self.lo1, self.mem = gpr, hi, lo, hi1, lo1, bytearray(mem)
        self.vumem = bytearray(vumem if vumem is not None else bytes(VU0_DATA_BYTES))
        self.fpr, self.acc, self.fcr31 = list(fpr or [0] * 32), acc, fcr31
        vu = list(vu or [0] * VU_WORDS)
        self.vf = [vu[4 * r:4 * r + 4] for r in range(32)]
        self.vacc, (self.q, self.i, self.clip, self.mac, self.status), self.vi = vu[128:132], vu[132:137], vu[137:153]

    def vu_words(self):
        return [w for r in self.vf for w in r] + self.vacc + [self.q, self.i, self.clip, self.mac, self.status] + self.vi

    def copy(self):
        out = State([list(r) for r in self.gpr], self.hi, self.lo, self.hi1, self.lo1, self.mem,
                    self.fpr, self.acc, self.fcr31, self.vu_words(), self.vumem)
        for key, value in self.__dict__.items():  # extra scalar state (VU microprogram P, R, TOP, ...)
            out.__dict__.setdefault(key, value)
        return out

    def get(self, r):
        return 0 if r == 0 else self.gpr[r][0]

    def set(self, r, value):
        if r:
            self.gpr[r][0] = value & M64

    def set128(self, r, low, high):
        if r:
            self.gpr[r] = [low & M64, high & M64]

    @staticmethod
    def _offset(address, size=1):
        if not (DATA_BASE <= address and address + size <= DATA_BASE + DATA_BYTES):
            raise IndexError(f'{address:#x} is outside the data window')
        return address - DATA_BASE

    def byte(self, address):
        return self.mem[self._offset(address)]

    def put(self, address, value):
        self.mem[self._offset(address)] = value & 0xFF

    def load(self, address, size):
        at = self._offset(address, size)
        return int.from_bytes(self.mem[at:at + size], 'little')

    def store(self, address, size, value):
        at = self._offset(address, size)
        self.mem[at:at + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little')


class Trap(Exception):
    """The input would raise a CPU exception; such inputs are not part of the check."""


# ---- encoders ---------------------------------------------------------------------------------
def r_type(funct, rs=0, rt=0, rd=0, sa=0, op=0):
    return (op << 26) | (rs << 21) | (rt << 16) | (rd << 11) | (sa << 6) | funct


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


# ---- reference semantics (R5900 manual) ---------------------------------------------------------
def _mul_result(s, rd, product, pipe):
    lo, hi = s32(product), s32(product >> 32)
    if pipe:
        s.lo1, s.hi1 = lo, hi
    else:
        s.lo, s.hi = lo, hi
    s.set(rd, lo)


def _div_result(s, n, d, signed, pipe):
    if signed:
        n, d = sext(n, 32), sext(d, 32)
        if d == 0:
            q, r = (-1 if n >= 0 else 1), n
        elif n == -(1 << 31) and d == -1:
            q, r = n, 0
        else:
            q = abs(n) // abs(d) * (1 if (n < 0) == (d < 0) else -1)
            r = n - q * d
    else:
        n, d = n & M32, d & M32
        q, r = (M32, n) if d == 0 else (n // d, n % d)
    if pipe:
        s.lo1, s.hi1 = s32(q), s32(r)
    else:
        s.lo, s.hi = s32(q), s32(r)


def _madd(s, rd, rs, rt, signed, pipe):
    hi, lo = (s.hi1, s.lo1) if pipe else (s.hi, s.lo)
    acc = ((hi & M32) << 32) | (lo & M32)
    a, b = (sext(rs, 32), sext(rt, 32)) if signed else (rs & M32, rt & M32)
    _mul_result(s, rd, (acc + a * b) & M64, pipe)


def _add32(a, b):
    result = sext(a, 32) + sext(b, 32)
    if not -(1 << 31) <= result < (1 << 31):
        raise Trap
    return s32(result)


def _add64(a, b):
    result = signed64(a) + signed64(b)
    if not -(1 << 63) <= result < (1 << 63):
        raise Trap
    return result & M64


def _sub32(a, b):
    result = sext(a, 32) - sext(b, 32)
    if not -(1 << 31) <= result < (1 << 31):
        raise Trap
    return s32(result)


def _sub64(a, b):
    result = signed64(a) - signed64(b)
    if not -(1 << 63) <= result < (1 << 63):
        raise Trap
    return result & M64


# name: (kind, encoder fields, semantics(state, fields))
# kinds: r3 (rd, rs, rt), sh (rd, rt, sa), r2 (rd, rs, rt) ..., imm (rt, rs, imm), mem (rt, base, offset)
def _special(funct, fn, layout='r3'):
    return dict(layout=layout, encode=lambda f: r_type(funct, f.get('rs', 0), f.get('rt', 0), f.get('rd', 0), f.get('sa', 0)), run=fn)


def _mmi(funct, fn, layout='r3'):
    return dict(layout=layout, encode=lambda f: r_type(funct, f.get('rs', 0), f.get('rt', 0), f.get('rd', 0), 0, op=0x1C), run=fn)


def _imm(op, fn):
    return dict(layout='imm', encode=lambda f: i_type(op, f['rs'], f['rt'], f['imm']), run=fn)


def _mem(op, size, fn, align):
    return dict(layout='mem', size=size, align=align, encode=lambda f: i_type(op, f['rs'], f['rt'], f['imm']), run=fn)


def _ea(s, f):
    return (s.get(f['rs']) + sext(f['imm'], 16)) & M32


def _lwl(s, f):
    a = _ea(s, f); b = a & 3; v = s.get(f['rt']) & M32
    for i in range(b + 1):
        shift = 8 * (3 - i)
        v = (v & ~(0xFF << shift)) | (s.byte(a - i) << shift)
    s.set(f['rt'], s32(v))


def _lwr(s, f):
    a = _ea(s, f); b = a & 3; full = s.get(f['rt']); v = full & M32
    for i in range(4 - b):
        v = (v & ~(0xFF << (8 * i))) | (s.byte(a + i) << (8 * i))
    s.set(f['rt'], s32(v) if b == 0 else (full & ~M32) | v)


def _ldl(s, f):
    a = _ea(s, f); b = a & 7; v = s.get(f['rt'])
    for i in range(b + 1):
        shift = 8 * (7 - i)
        v = (v & ~(0xFF << shift)) | (s.byte(a - i) << shift)
    s.set(f['rt'], v)


def _ldr(s, f):
    a = _ea(s, f); b = a & 7; v = s.get(f['rt'])
    for i in range(8 - b):
        v = (v & ~(0xFF << (8 * i))) | (s.byte(a + i) << (8 * i))
    s.set(f['rt'], v)


def _swl(s, f):
    a = _ea(s, f); v = s.get(f['rt'])
    for i in range((a & 3) + 1):
        s.put(a - i, v >> (8 * (3 - i)))


def _swr(s, f):
    a = _ea(s, f); v = s.get(f['rt'])
    for i in range(4 - (a & 3)):
        s.put(a + i, v >> (8 * i))


def _sdl(s, f):
    a = _ea(s, f); v = s.get(f['rt'])
    for i in range((a & 7) + 1):
        s.put(a - i, v >> (8 * (7 - i)))


def _sdr(s, f):
    a = _ea(s, f); v = s.get(f['rt'])
    for i in range(8 - (a & 7)):
        s.put(a + i, v >> (8 * i))


def _lq(s, f):
    a = _ea(s, f) & ~15
    s.set128(f['rt'], s.load(a, 8), s.load(a + 8, 8))


def _sq(s, f):
    a = _ea(s, f) & ~15
    low, high = (0, 0) if f['rt'] == 0 else s.gpr[f['rt']]
    s.store(a, 8, low); s.store(a + 8, 8, high)


def _load(size, signed):
    def run(s, f):
        value = s.load(_ea(s, f), size)
        s.set(f['rt'], (sext(value, 8 * size) & M64) if signed else value)
    return run


def _store(size):
    return lambda s, f: s.store(_ea(s, f), size, s.get(f['rt']))


def _set(rd_value):
    return lambda s, f: s.set(f['rd'], rd_value(s, f))


g = lambda s, f, k: s.get(f[k])
FORMS = {
    'sll': _special(0x00, _set(lambda s, f: s32(g(s, f, 'rt') << f['sa'])), 'sh'),
    'srl': _special(0x02, _set(lambda s, f: s32((g(s, f, 'rt') & M32) >> f['sa'])), 'sh'),
    'sra': _special(0x03, _set(lambda s, f: s32(sext(g(s, f, 'rt'), 32) >> f['sa'])), 'sh'),
    'sllv': _special(0x04, _set(lambda s, f: s32(g(s, f, 'rt') << (g(s, f, 'rs') & 31)))),
    'srlv': _special(0x06, _set(lambda s, f: s32((g(s, f, 'rt') & M32) >> (g(s, f, 'rs') & 31)))),
    'srav': _special(0x07, _set(lambda s, f: s32(sext(g(s, f, 'rt'), 32) >> (g(s, f, 'rs') & 31)))),
    'movz': _special(0x0A, lambda s, f: s.set(f['rd'], g(s, f, 'rs')) if g(s, f, 'rt') == 0 else None),
    'movn': _special(0x0B, lambda s, f: s.set(f['rd'], g(s, f, 'rs')) if g(s, f, 'rt') != 0 else None),
    'mfhi': _special(0x10, lambda s, f: s.set(f['rd'], s.hi), 'rd'),
    'mthi': _special(0x11, lambda s, f: setattr(s, 'hi', g(s, f, 'rs')), 'rs'),
    'mflo': _special(0x12, lambda s, f: s.set(f['rd'], s.lo), 'rd'),
    'mtlo': _special(0x13, lambda s, f: setattr(s, 'lo', g(s, f, 'rs')), 'rs'),
    'dsllv': _special(0x14, _set(lambda s, f: g(s, f, 'rt') << (g(s, f, 'rs') & 63))),
    'dsrlv': _special(0x16, _set(lambda s, f: g(s, f, 'rt') >> (g(s, f, 'rs') & 63))),
    'dsrav': _special(0x17, _set(lambda s, f: signed64(g(s, f, 'rt')) >> (g(s, f, 'rs') & 63))),
    'mult': _special(0x18, lambda s, f: _mul_result(s, f['rd'], sext(g(s, f, 'rs'), 32) * sext(g(s, f, 'rt'), 32), False)),
    'multu': _special(0x19, lambda s, f: _mul_result(s, f['rd'], (g(s, f, 'rs') & M32) * (g(s, f, 'rt') & M32), False)),
    'div': _special(0x1A, lambda s, f: _div_result(s, g(s, f, 'rs'), g(s, f, 'rt'), True, False), 'rs_rt'),
    'divu': _special(0x1B, lambda s, f: _div_result(s, g(s, f, 'rs'), g(s, f, 'rt'), False, False), 'rs_rt'),
    'add': _special(0x20, _set(lambda s, f: _add32(g(s, f, 'rs'), g(s, f, 'rt')))),
    'addu': _special(0x21, _set(lambda s, f: s32(g(s, f, 'rs') + g(s, f, 'rt')))),
    'sub': _special(0x22, _set(lambda s, f: _sub32(g(s, f, 'rs'), g(s, f, 'rt')))),
    'subu': _special(0x23, _set(lambda s, f: s32(g(s, f, 'rs') - g(s, f, 'rt')))),
    'and': _special(0x24, _set(lambda s, f: g(s, f, 'rs') & g(s, f, 'rt'))),
    'or': _special(0x25, _set(lambda s, f: g(s, f, 'rs') | g(s, f, 'rt'))),
    'xor': _special(0x26, _set(lambda s, f: g(s, f, 'rs') ^ g(s, f, 'rt'))),
    'nor': _special(0x27, _set(lambda s, f: ~(g(s, f, 'rs') | g(s, f, 'rt')) & M64)),
    'slt': _special(0x2A, _set(lambda s, f: int(signed64(g(s, f, 'rs')) < signed64(g(s, f, 'rt'))))),
    'sltu': _special(0x2B, _set(lambda s, f: int(g(s, f, 'rs') < g(s, f, 'rt')))),
    'dadd': _special(0x2C, _set(lambda s, f: _add64(g(s, f, 'rs'), g(s, f, 'rt')))),
    'daddu': _special(0x2D, _set(lambda s, f: g(s, f, 'rs') + g(s, f, 'rt'))),
    'dsub': _special(0x2E, _set(lambda s, f: _sub64(g(s, f, 'rs'), g(s, f, 'rt')))),
    'dsubu': _special(0x2F, _set(lambda s, f: g(s, f, 'rs') - g(s, f, 'rt'))),
    'dsll': _special(0x38, _set(lambda s, f: g(s, f, 'rt') << f['sa']), 'sh'),
    'dsrl': _special(0x3A, _set(lambda s, f: g(s, f, 'rt') >> f['sa']), 'sh'),
    'dsra': _special(0x3B, _set(lambda s, f: signed64(g(s, f, 'rt')) >> f['sa']), 'sh'),
    'dsll32': _special(0x3C, _set(lambda s, f: g(s, f, 'rt') << (f['sa'] + 32)), 'sh'),
    'dsrl32': _special(0x3E, _set(lambda s, f: g(s, f, 'rt') >> (f['sa'] + 32)), 'sh'),
    'dsra32': _special(0x3F, _set(lambda s, f: signed64(g(s, f, 'rt')) >> (f['sa'] + 32)), 'sh'),
    'addi': _imm(0x08, lambda s, f: s.set(f['rt'], _add32(g(s, f, 'rs'), sext(f['imm'], 16)))),
    'addiu': _imm(0x09, lambda s, f: s.set(f['rt'], s32(g(s, f, 'rs') + sext(f['imm'], 16)))),
    'slti': _imm(0x0A, lambda s, f: s.set(f['rt'], int(signed64(g(s, f, 'rs')) < sext(f['imm'], 16)))),
    'sltiu': _imm(0x0B, lambda s, f: s.set(f['rt'], int(g(s, f, 'rs') < (sext(f['imm'], 16) & M64)))),
    'andi': _imm(0x0C, lambda s, f: s.set(f['rt'], g(s, f, 'rs') & f['imm'])),
    'ori': _imm(0x0D, lambda s, f: s.set(f['rt'], g(s, f, 'rs') | f['imm'])),
    'xori': _imm(0x0E, lambda s, f: s.set(f['rt'], g(s, f, 'rs') ^ f['imm'])),
    'lui': _imm(0x0F, lambda s, f: s.set(f['rt'], s32(f['imm'] << 16))),
    'daddi': _imm(0x18, lambda s, f: s.set(f['rt'], _add64(g(s, f, 'rs'), sext(f['imm'], 16) & M64))),
    'daddiu': _imm(0x19, lambda s, f: s.set(f['rt'], g(s, f, 'rs') + sext(f['imm'], 16))),
    'lb': _mem(0x20, 1, _load(1, True), 1), 'lh': _mem(0x21, 2, _load(2, True), 2),
    'lw': _mem(0x23, 4, _load(4, True), 4), 'lbu': _mem(0x24, 1, _load(1, False), 1),
    'lhu': _mem(0x25, 2, _load(2, False), 2), 'lwu': _mem(0x27, 4, _load(4, False), 4),
    'ld': _mem(0x37, 8, _load(8, False), 8), 'lq': _mem(0x1E, 16, _lq, 1),
    'lwl': _mem(0x22, 4, _lwl, 1), 'lwr': _mem(0x26, 4, _lwr, 1),
    'ldl': _mem(0x1A, 8, _ldl, 1), 'ldr': _mem(0x1B, 8, _ldr, 1),
    'sb': _mem(0x28, 1, _store(1), 1), 'sh': _mem(0x29, 2, _store(2), 2),
    'sw': _mem(0x2B, 4, _store(4), 4), 'sd': _mem(0x3F, 8, _store(8), 8), 'sq': _mem(0x1F, 16, _sq, 1),
    'swl': _mem(0x2A, 4, _swl, 1), 'swr': _mem(0x2E, 4, _swr, 1),
    'sdl': _mem(0x2C, 8, _sdl, 1), 'sdr': _mem(0x2D, 8, _sdr, 1),
    'madd': _mmi(0x00, lambda s, f: _madd(s, f['rd'], g(s, f, 'rs'), g(s, f, 'rt'), True, False)),
    'maddu': _mmi(0x01, lambda s, f: _madd(s, f['rd'], g(s, f, 'rs'), g(s, f, 'rt'), False, False)),
    'mfhi1': _mmi(0x10, lambda s, f: s.set(f['rd'], s.hi1), 'rd'),
    'mthi1': _mmi(0x11, lambda s, f: setattr(s, 'hi1', g(s, f, 'rs')), 'rs'),
    'mflo1': _mmi(0x12, lambda s, f: s.set(f['rd'], s.lo1), 'rd'),
    'mtlo1': _mmi(0x13, lambda s, f: setattr(s, 'lo1', g(s, f, 'rs')), 'rs'),
    'mult1': _mmi(0x18, lambda s, f: _mul_result(s, f['rd'], sext(g(s, f, 'rs'), 32) * sext(g(s, f, 'rt'), 32), True)),
    'multu1': _mmi(0x19, lambda s, f: _mul_result(s, f['rd'], (g(s, f, 'rs') & M32) * (g(s, f, 'rt') & M32), True)),
    'div1': _mmi(0x1A, lambda s, f: _div_result(s, g(s, f, 'rs'), g(s, f, 'rt'), True, True), 'rs_rt'),
    'divu1': _mmi(0x1B, lambda s, f: _div_result(s, g(s, f, 'rs'), g(s, f, 'rt'), False, True), 'rs_rt'),
    'madd1': _mmi(0x20, lambda s, f: _madd(s, f['rd'], g(s, f, 'rs'), g(s, f, 'rt'), True, True)),
    'maddu1': _mmi(0x21, lambda s, f: _madd(s, f['rd'], g(s, f, 'rs'), g(s, f, 'rt'), False, True)),
}


# ---- 128-bit multimedia (MMI) forms -------------------------------------------------------------
# HI and LO are 128 bits wide on the EE; the context keeps their upper halves in hi1/lo1.
def q(s, r):
    return 0 if r == 0 else s.gpr[r][0] | (s.gpr[r][1] << 64)


def setq(s, r, value):
    s.set128(r, value & M64, (value >> 64) & M64)


def lanes(value, bits):
    mask = (1 << bits) - 1
    return [(value >> (bits * i)) & mask for i in range(128 // bits)]


def join(values, bits):
    mask = (1 << bits) - 1
    return sum((v & mask) << (bits * i) for i, v in enumerate(values))


def _hilo(s):
    return s.hi | (s.hi1 << 64), s.lo | (s.lo1 << 64)


def _set_hilo(s, hi, lo):
    s.hi, s.hi1, s.lo, s.lo1 = hi & M64, (hi >> 64) & M64, lo & M64, (lo >> 64) & M64


def _sat(value, bits, signed=True):
    low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    return min(max(value, low), high)


def _group(sub_funct, group, fn, layout='r3'):
    return dict(layout=layout, encode=lambda f: r_type(group, f.get('rs', 0), f.get('rt', 0), f.get('rd', 0), sub_funct, op=0x1C), run=fn)


def _rd128(value):
    return lambda s, f: setq(s, f['rd'], value(q(s, f['rs']), q(s, f['rt'])))


def _lanewise(bits, op):
    return _rd128(lambda a, b: join([op(x, y) for x, y in zip(lanes(a, bits), lanes(b, bits))], bits))


def _signed_lanes(bits, op):
    return _lanewise(bits, lambda x, y: op(sext(x, bits), sext(y, bits)))


def _mask(bits, condition):
    return _signed_lanes(bits, lambda x, y: (1 << bits) - 1 if condition(x, y) else 0)


def _interleave(pick, bits):
    """rd lanes from (source, lane) pairs; source 0 is rs and 1 is rt."""
    return _rd128(lambda a, b: join([lanes((a, b)[src], bits)[lane] for src, lane in pick], bits))


def _rt_lanes(order, bits):
    return _rd128(lambda a, b: join([lanes(b, bits)[i] for i in order], bits))


def _shift_lanes(bits, op):
    return lambda s, f: setq(s, f['rd'], join([op(x, f['sa'] & (bits - 1)) for x in lanes(q(s, f['rt']), bits)], bits))


def _variable_shift(op):
    # Even words of rt shifted by the low five bits of the matching rs word; 32-bit results sign-extended.
    def run(s, f):
        a, b = lanes(q(s, f['rs']), 32), lanes(q(s, f['rt']), 32)
        setq(s, f['rd'], join([s32(op(b[0], a[0] & 31)), s32(op(b[2], a[2] & 31))], 64))
    return run


def _word_multiply(signed, accumulate=0):
    def run(s, f):
        a, b = lanes(q(s, f['rs']), 32), lanes(q(s, f['rt']), 32)
        hi, lo = lanes(_hilo(s)[0], 64), lanes(_hilo(s)[1], 64)
        rd, new_hi, new_lo = [], [], []
        for half in (0, 1):
            x, y = a[2 * half], b[2 * half]
            product = sext(x, 32) * sext(y, 32) if signed else x * y
            total = ((((hi[half] & M32) << 32) | (lo[half] & M32)) + accumulate * product) & M64 if accumulate else product & M64
            rd.append(total); new_lo.append(s32(total)); new_hi.append(s32(total >> 32))
        _set_hilo(s, join(new_hi, 64), join(new_lo, 64))
        setq(s, f['rd'], join(rd, 64))
    return run


def _word_divide(signed):
    def run(s, f):
        a, b = lanes(q(s, f['rs']), 32), lanes(q(s, f['rt']), 32)
        scratch = State([[0, 0] for _ in range(32)], 0, 0, 0, 0, b'')
        quotients, remainders = [], []
        for half in (0, 1):
            _div_result(scratch, a[2 * half], b[2 * half], signed, False)
            quotients.append(scratch.lo); remainders.append(scratch.hi)
        _set_hilo(s, join(remainders, 64), join(quotients, 64))
    return run


def _halfword_multiply(mode):
    # LO/HI words take products of halfword pairs in the order LO0 HI0 ... as the EE manual lays out.
    def run(s, f):
        a = [sext(x, 16) for x in lanes(q(s, f['rs']), 16)]
        b = [sext(x, 16) for x in lanes(q(s, f['rt']), 16)]
        hi, lo = (lanes(v, 32) for v in _hilo(s))
        p = [a[i] * b[i] for i in range(8)]
        targets = [(lo, 0), (lo, 1), (hi, 0), (hi, 1), (lo, 2), (lo, 3), (hi, 2), (hi, 3)]
        for i, (reg, lane) in enumerate(targets):
            reg[lane] = (reg[lane] + p[i] if mode == 'add' else reg[lane] - p[i] if mode == 'sub' else p[i]) & M32
        _set_hilo(s, join(hi, 32), join(lo, 32))
        setq(s, f['rd'], join([lo[0], hi[0], lo[2], hi[2]], 32))
    return run


def _horizontal_multiply(subtract):
    def run(s, f):
        a = [sext(x, 16) for x in lanes(q(s, f['rs']), 16)]
        b = [sext(x, 16) for x in lanes(q(s, f['rt']), 16)]
        firsts = [a[2 * i + 1] * b[2 * i + 1] for i in range(4)]
        sums = [(firsts[i] - a[2 * i] * b[2 * i] if subtract else firsts[i] + a[2 * i] * b[2 * i]) & M32 for i in range(4)]
        # The odd words are undefined in the manual; PCSX2 records the hardware leaving the first
        # product there (inverted for the subtracting form).
        odd = [(~x if subtract else x) & M32 for x in firsts]
        hi, lo = [0] * 4, [0] * 4
        lo[0], lo[1], hi[0], hi[1], lo[2], lo[3], hi[2], hi[3] = sums[0], odd[0], sums[1], odd[1], sums[2], odd[2], sums[3], odd[3]
        _set_hilo(s, join(hi, 32), join(lo, 32))
        setq(s, f['rd'], join(sums, 32))
    return run


def _pdivbw(s, f):
    a = [sext(x, 32) for x in lanes(q(s, f['rs']), 32)]
    d = sext(q(s, f['rt']), 16)
    quotients, remainders = [], []
    for n in a:
        if d == 0:
            qv, r = (-1 if n >= 0 else 1), n
        elif n == -(1 << 31) and d == -1:
            qv, r = n, 0
        else:
            qv = abs(n) // abs(d) * (1 if (n < 0) == (d < 0) else -1)
            r = n - qv * d
        quotients.append(qv); remainders.append(r)
    _set_hilo(s, join(remainders, 32), join(quotients, 32))


def _plzcw(s, f):
    words = lanes(q(s, f['rs']), 32)[:2]
    counts = []
    for w in words:
        sign, n = w >> 31, 0
        while n < 32 and ((w >> (31 - n)) & 1) == sign:
            n += 1
        counts.append(n - 1)
    s.set(f['rd'], join(counts, 32))


def _pmfhl(kind):
    return lambda s, f: _pmfhl_run(s, f, kind)


def _pmfhl_run(s, f, kind):
    hi, lo = _hilo(s)
    h, l = lanes(hi, 32), lanes(lo, 32)
    if kind == 0:
        value = join([l[0], h[0], l[2], h[2]], 32)
    elif kind == 1:
        value = join([l[1], h[1], l[3], h[3]], 32)
    elif kind == 2:
        value = join([_sat(sext((h[2 * i] << 32) | l[2 * i], 64), 32) & M64 for i in (0, 1)], 64)
    elif kind == 3:
        lh, hh = lanes(lo, 16), lanes(hi, 16)
        value = join([lh[0], lh[2], hh[0], hh[2], lh[4], lh[6], hh[4], hh[6]], 16)
    else:
        value = join([_sat(sext(x, 32), 16) for x in (l[0], l[1], h[0], h[1], l[2], l[3], h[2], h[3])], 16)
    setq(s, f['rd'], value)


def _pmthl(s, f):
    w = lanes(q(s, f['rs']), 32)
    hi, lo = (lanes(v, 32) for v in _hilo(s))
    lo[0], hi[0], lo[2], hi[2] = w
    _set_hilo(s, join(hi, 32), join(lo, 32))


def _pext5(a, b):
    out = []
    for w in lanes(b, 32):
        out.append(((w & 0x1F) << 3) | (((w >> 5) & 0x1F) << 11) | (((w >> 10) & 0x1F) << 19) | (((w >> 15) & 1) << 31))
    return join(out, 32)


def _ppac5(a, b):
    out = []
    for w in lanes(b, 32):
        out.append(((w >> 3) & 0x1F) | (((w >> 11) & 0x1F) << 5) | (((w >> 19) & 0x1F) << 10) | (((w >> 31) & 1) << 15))
    return join(out, 32)


def _abs(bits):
    return _lanewise(bits, lambda x, y: min(abs(sext(y, bits)), (1 << (bits - 1)) - 1))


def _pinth():
    return _interleave([p for i in range(4) for p in ((1, i), (0, 4 + i))], 16)


def _mmi_shift(funct, fn):
    return dict(layout='sh', encode=lambda f: r_type(funct, 0, f['rt'], f['rd'], f['sa'], op=0x1C), run=fn)


def _mmi0(sub, fn, layout='r3'):
    return _group(sub, 0x08, fn, layout)


def _mmi1(sub, fn, layout='r3'):
    return _group(sub, 0x28, fn, layout)


def _mmi2(sub, fn, layout='r3'):
    return _group(sub, 0x09, fn, layout)


def _mmi3(sub, fn, layout='r3'):
    return _group(sub, 0x29, fn, layout)


def _wrap(bits, op):
    return _lanewise(bits, op)


def _saturating(bits, op, signed):
    return _lanewise(bits, lambda x, y: _sat(op(sext(x, bits), sext(y, bits)) if signed else op(x, y), bits, signed))


def _extend(bits, upper):
    count = 64 // bits
    base = count if upper else 0
    return _interleave([p for i in range(count) for p in ((1, base + i), (0, base + i))], bits)


def _pack(bits):
    count = 64 // bits
    return _interleave([(1, 2 * i) for i in range(count)] + [(0, 2 * i) for i in range(count)], bits)


MMI_FORMS = {
    'plzcw': _group(0, 0x04, _plzcw, 'rd_rs'),
    'pmfhl.lw': _group(0, 0x30, _pmfhl(0), 'rd'), 'pmfhl.uw': _group(1, 0x30, _pmfhl(1), 'rd'),
    'pmfhl.slw': _group(2, 0x30, _pmfhl(2), 'rd'), 'pmfhl.lh': _group(3, 0x30, _pmfhl(3), 'rd'),
    'pmfhl.sh': _group(4, 0x30, _pmfhl(4), 'rd'),
    'pmthl.lw': _group(0, 0x31, _pmthl, 'rs'),
    'psllh': _mmi_shift(0x34, _shift_lanes(16, lambda x, n: x << n)),
    'psrlh': _mmi_shift(0x36, _shift_lanes(16, lambda x, n: x >> n)),
    'psrah': _mmi_shift(0x37, _shift_lanes(16, lambda x, n: sext(x, 16) >> n)),
    'psllw': _mmi_shift(0x3C, _shift_lanes(32, lambda x, n: x << n)),
    'psrlw': _mmi_shift(0x3E, _shift_lanes(32, lambda x, n: x >> n)),
    'psraw': _mmi_shift(0x3F, _shift_lanes(32, lambda x, n: sext(x, 32) >> n)),
    # MMI0
    'paddw': _mmi0(0x00, _wrap(32, lambda x, y: x + y)), 'psubw': _mmi0(0x01, _wrap(32, lambda x, y: x - y)),
    'pcgtw': _mmi0(0x02, _mask(32, lambda x, y: x > y)), 'pmaxw': _mmi0(0x03, _signed_lanes(32, max)),
    'paddh': _mmi0(0x04, _wrap(16, lambda x, y: x + y)), 'psubh': _mmi0(0x05, _wrap(16, lambda x, y: x - y)),
    'pcgth': _mmi0(0x06, _mask(16, lambda x, y: x > y)), 'pmaxh': _mmi0(0x07, _signed_lanes(16, max)),
    'paddb': _mmi0(0x08, _wrap(8, lambda x, y: x + y)), 'psubb': _mmi0(0x09, _wrap(8, lambda x, y: x - y)),
    'pcgtb': _mmi0(0x0A, _mask(8, lambda x, y: x > y)),
    'paddsw': _mmi0(0x10, _saturating(32, lambda x, y: x + y, True)),
    'psubsw': _mmi0(0x11, _saturating(32, lambda x, y: x - y, True)),
    'pextlw': _mmi0(0x12, _extend(32, False)), 'ppacw': _mmi0(0x13, _pack(32)),
    'paddsh': _mmi0(0x14, _saturating(16, lambda x, y: x + y, True)),
    'psubsh': _mmi0(0x15, _saturating(16, lambda x, y: x - y, True)),
    'pextlh': _mmi0(0x16, _extend(16, False)), 'ppach': _mmi0(0x17, _pack(16)),
    'paddsb': _mmi0(0x18, _saturating(8, lambda x, y: x + y, True)),
    'psubsb': _mmi0(0x19, _saturating(8, lambda x, y: x - y, True)),
    'pextlb': _mmi0(0x1A, _extend(8, False)), 'ppacb': _mmi0(0x1B, _pack(8)),
    'pext5': _mmi0(0x1E, _rd128(_pext5), 'rd_rt'), 'ppac5': _mmi0(0x1F, _rd128(_ppac5), 'rd_rt'),
    # MMI1
    'pabsw': _mmi1(0x01, _abs(32), 'rd_rt'), 'pceqw': _mmi1(0x02, _mask(32, lambda x, y: x == y)),
    'pminw': _mmi1(0x03, _signed_lanes(32, min)),
    'padsbh': _mmi1(0x04, _rd128(lambda a, b: join([(x - y) if i < 4 else (x + y) for i, (x, y) in
                                                    enumerate(zip(lanes(a, 16), lanes(b, 16)))], 16))),
    'pabsh': _mmi1(0x05, _abs(16), 'rd_rt'), 'pceqh': _mmi1(0x06, _mask(16, lambda x, y: x == y)),
    'pminh': _mmi1(0x07, _signed_lanes(16, min)), 'pceqb': _mmi1(0x0A, _mask(8, lambda x, y: x == y)),
    'padduw': _mmi1(0x10, _saturating(32, lambda x, y: x + y, False)),
    'psubuw': _mmi1(0x11, _saturating(32, lambda x, y: x - y, False)), 'pextuw': _mmi1(0x12, _extend(32, True)),
    'padduh': _mmi1(0x14, _saturating(16, lambda x, y: x + y, False)),
    'psubuh': _mmi1(0x15, _saturating(16, lambda x, y: x - y, False)), 'pextuh': _mmi1(0x16, _extend(16, True)),
    'paddub': _mmi1(0x18, _saturating(8, lambda x, y: x + y, False)),
    'psubub': _mmi1(0x19, _saturating(8, lambda x, y: x - y, False)), 'pextub': _mmi1(0x1A, _extend(8, True)),
    # MMI2
    'pmaddw': _mmi2(0x00, _word_multiply(True, 1)), 'psllvw': _mmi2(0x02, _variable_shift(lambda x, n: x << n)),
    'psrlvw': _mmi2(0x03, _variable_shift(lambda x, n: x >> n)), 'pmsubw': _mmi2(0x04, _word_multiply(True, -1)),
    'pmfhi': _mmi2(0x08, lambda s, f: setq(s, f['rd'], _hilo(s)[0]), 'rd'),
    'pmflo': _mmi2(0x09, lambda s, f: setq(s, f['rd'], _hilo(s)[1]), 'rd'),
    'pinth': _mmi2(0x0A, _pinth()), 'pmultw': _mmi2(0x0C, _word_multiply(True)),
    'pdivw': _mmi2(0x0D, _word_divide(True), 'rs_rt'),
    'pcpyld': _mmi2(0x0E, _interleave([(1, 0), (0, 0)], 64)),
    'pmaddh': _mmi2(0x10, _halfword_multiply('add')), 'phmadh': _mmi2(0x11, _horizontal_multiply(False)),
    'pand': _mmi2(0x12, _rd128(lambda a, b: a & b)), 'pxor': _mmi2(0x13, _rd128(lambda a, b: a ^ b)),
    'pmsubh': _mmi2(0x14, _halfword_multiply('sub')), 'phmsbh': _mmi2(0x15, _horizontal_multiply(True)),
    'pexeh': _mmi2(0x1A, _rt_lanes([2, 1, 0, 3, 6, 5, 4, 7], 16), 'rd_rt'),
    'prevh': _mmi2(0x1B, _rt_lanes([3, 2, 1, 0, 7, 6, 5, 4], 16), 'rd_rt'),
    'pmulth': _mmi2(0x1C, _halfword_multiply('set')), 'pdivbw': _mmi2(0x1D, _pdivbw, 'rs_rt'),
    'pexew': _mmi2(0x1E, _rt_lanes([2, 1, 0, 3], 32), 'rd_rt'),
    'prot3w': _mmi2(0x1F, _rt_lanes([1, 2, 0, 3], 32), 'rd_rt'),
    # MMI3
    'pmadduw': _mmi3(0x00, _word_multiply(False, 1)),
    'psravw': _mmi3(0x03, _variable_shift(lambda x, n: sext(x, 32) >> n)),
    'pmthi': _mmi3(0x08, lambda s, f: _set_hilo(s, q(s, f['rs']), _hilo(s)[1]), 'rs'),
    'pmtlo': _mmi3(0x09, lambda s, f: _set_hilo(s, _hilo(s)[0], q(s, f['rs'])), 'rs'),
    'pinteh': _mmi3(0x0A, _interleave([p for i in range(4) for p in ((1, 2 * i), (0, 2 * i))], 16)),
    'pmultuw': _mmi3(0x0C, _word_multiply(False)), 'pdivuw': _mmi3(0x0D, _word_divide(False), 'rs_rt'),
    'pcpyud': _mmi3(0x0E, _interleave([(0, 1), (1, 1)], 64)),
    'por': _mmi3(0x12, _rd128(lambda a, b: a | b)), 'pnor': _mmi3(0x13, _rd128(lambda a, b: ~(a | b))),
    'pexch': _mmi3(0x1A, _rt_lanes([0, 2, 1, 3, 4, 6, 5, 7], 16), 'rd_rt'),
    'pcpyh': _mmi3(0x1B, _rt_lanes([0, 0, 0, 0, 4, 4, 4, 4], 16), 'rd_rt'),
    'pexcw': _mmi3(0x1E, _rt_lanes([0, 2, 1, 3], 32), 'rd_rt'),
}
FORMS.update(MMI_FORMS)

# QFSRV funnels rs:rt right by the SA register, which only MTSAB/MTSAH set; each case sets it first
# from $zero and the immediate (`mtsab $0, imm` gives SA = (imm & 15) bytes, `mtsah` (imm & 7) halfwords).
SA_SEQUENCES = {
    'mtsab+qfsrv': (0x18, lambda imm: 8 * (imm & 15)),
    'mtsah+qfsrv': (0x19, lambda imm: 16 * (imm & 7)),
}


def _qfsrv_words(setter, f):
    qfsrv = r_type(0x28, f['rs'], f['rt'], f['rd'], 0x1B, op=0x1C)
    return [i_type(0x01, 0, setter, f['imm']), qfsrv]


def _qfsrv_run(amount):
    def run(s, f):
        setq(s, f['rd'], (((q(s, f['rs']) << 128) | q(s, f['rt'])) >> amount(f['imm'])) & ((1 << 128) - 1))
    return run


# ---- FPU (COP1) forms ---------------------------------------------------------------------------
# Arithmetic results come from tools/reference/ps2float.c, the bit-exact integer model of the EE
# FPU (flush-to-zero, no infinities or NaNs, round toward zero, hardware multiplier and divider),
# compiled on first use. Moves, sign changes, compares and conversions follow the instruction set.
# Compared: the 32 registers, ACC and the condition bit; the sticky exception flags are not.
FCR31_C = 1 << 23
_PS2FLOAT = []


def ps2float():
    if not _PS2FLOAT:
        import ctypes
        import hashlib
        source = Path(__file__).resolve().parent / 'tools/reference/ps2float.c'
        tag = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        library = Path(tempfile.gettempdir()) / f'behaviour-check-ps2float-{tag}.so'
        if not library.exists():
            compiler = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
            partial = library.with_suffix('.tmp')
            subprocess.run([compiler, '-std=c99', '-O2', '-shared', '-fPIC', str(source), '-o', str(partial)],
                           check=True, capture_output=True, timeout=120)
            partial.replace(library)
        lib = ctypes.CDLL(str(library))
        for name in ('add', 'sub', 'mul', 'div', 'rsqrt'):
            getattr(lib, 'ps2f_' + name).argtypes = [ctypes.c_uint32] * 2
        lib.ps2f_sqrt.argtypes = [ctypes.c_uint32]
        for name in ('madd', 'msub', 'madda', 'msuba'):
            getattr(lib, 'ps2f_' + name).argtypes = [ctypes.c_uint32] * 3 + [ctypes.c_int]
        for name in ('add', 'sub', 'mul', 'div', 'rsqrt', 'sqrt', 'madd', 'msub', 'madda', 'msuba'):
            getattr(lib, 'ps2f_' + name).restype = ctypes.c_uint64
        lib.ps2f_clip.argtypes = [ctypes.c_uint32] * 2
        lib.ps2f_clip.restype = ctypes.c_uint32
        lib.ps2f_compare.argtypes = [ctypes.c_uint32] * 2
        lib.ps2f_compare.restype = ctypes.c_int32
        _PS2FLOAT.append(lib)
    return _PS2FLOAT[0]


def _pf(name, *args):
    return getattr(ps2float(), 'ps2f_' + name)(*args) & M32


def _pff(name, *args):
    """(result, overflow, underflow) from the reference."""
    raw = getattr(ps2float(), 'ps2f_' + name)(*args)
    return raw & M32, bool(raw >> 32 & 1), bool(raw >> 33 & 1)


# FCR31: condition, cause flags I D O U and their sticky copies SI SD SO SU (PCSX2 FPU.cpp layout).
FCR_I, FCR_D, FCR_O, FCR_U = 1 << 17, 1 << 16, 1 << 15, 1 << 14
FCR_SI, FCR_SD, FCR_SO, FCR_SU = 1 << 6, 1 << 5, 1 << 4, 1 << 3
FCR31_FLAGS = (1 << 23) | FCR_I | FCR_D | FCR_O | FCR_U | FCR_SI | FCR_SD | FCR_SO | FCR_SU


def _fcr_arith(s, overflow, underflow):
    """Add/sub/mul and the multiply-add forms: overflow sets O and SO and leaves U; otherwise O clears
    and underflow sets U and SU or clears U."""
    if overflow:
        s.fcr31 |= FCR_O | FCR_SO
        return
    s.fcr31 &= ~FCR_O
    s.fcr31 = (s.fcr31 | FCR_U | FCR_SU) if underflow else (s.fcr31 & ~FCR_U)


_HOST_PRODUCT = []  # set while computing the host-rounding variant (see expected_host)


def _product(a, b):
    """The product inside a multiply-add. In the host-rounding variant a normal product is rounded to
    nearest, as the engine's host fast path does; the exact model truncates."""
    if _HOST_PRODUCT and (a >> 23) & 0xFF and (b >> 23) & 0xFF:
        return _nearest(_value(a) * _value(b))
    return _pf('mul', a, b)


def _value(bits):
    """A PS2 float as a Python float; exponent 255 is a number (up to 2^129), not Inf/NaN."""
    exponent = (bits >> 23) & 0xFF
    magnitude = 0.0 if exponent == 0 else ((1 << 23) | (bits & 0x7FFFFF)) * 2.0 ** (exponent - 150)
    return -magnitude if bits >> 31 else magnitude


def _nearest(value):
    """Round to the nearest PS2 float: overflow clamps to +-max, results below 2^-126 flush to zero."""
    sign = 0x80000000 if value < 0 else 0
    magnitude = abs(value)
    if magnitude < 2.0 ** -126:
        return sign
    exponent = max(1, min(255, int(math.floor(math.log2(magnitude))) + 127))
    mantissa = round(magnitude / 2.0 ** (exponent - 150))
    if mantissa >= 1 << 24:
        exponent, mantissa = exponent + 1, mantissa >> 1
    if exponent > 255:
        return sign | 0x7FFFFFFF
    return sign | (exponent << 23) | (mantissa & 0x7FFFFF)


def _denormal(bits):
    return bits & 0x7F800000 == 0 and bits & 0x7FFFFF != 0


def _ordered(a, b):
    """PS2 floats order as sign-magnitude integers (no NaNs); denormal operands are left out."""
    if _denormal(a) or _denormal(b):
        raise Trap
    return ps2float().ps2f_compare(a, b)


def _cop1(fmt, funct, fn, layout='fpu', unused=()):
    def encode(f):
        fields = {k: (0 if k in unused else f[k]) for k in ('ft', 'fs', 'fd')}
        return (0x11 << 26) | (fmt << 21) | (fields['ft'] << 16) | (fields['fs'] << 11) | (fields['fd'] << 6) | funct
    return dict(layout=layout, encode=encode, run=fn)


def _fd(value):
    def run(s, f):
        s.fpr[f['fd']] = value(s, s.fpr[f['fs']], s.fpr[f['ft']]) & M32
    return run


def _to_acc(value):
    def run(s, f):
        s.acc = value(s, s.fpr[f['fs']], s.fpr[f['ft']]) & M32
    return run


def _condition(test):
    def run(s, f):
        c = test(_ordered(s.fpr[f['fs']], s.fpr[f['ft']])) if test else False
        s.fcr31 = (s.fcr31 & ~FCR31_C) | (FCR31_C if c else 0)
    return run


def _pick(want_max):
    def value(s, a, b):
        order = _ordered(a, b)
        if order == 0 and a != b:
            raise Trap  # +0/-0 ties: which operand the hardware returns is not established
        return a if (order >= 0) == want_max else b
    return value


def _cvt_w(s, a, b):
    exponent = (a >> 23) & 0xFF
    if exponent == 0:
        return 0
    if exponent <= 0x9D:
        return int(struct.unpack('<f', struct.pack('<I', a))[0])
    return 0x80000000 if a >> 31 else 0x7FFFFFFF


def _cvt_s(s, a, b):
    value = sext(a, 32)
    if value == 0:
        return 0
    bits = struct.unpack('<I', struct.pack('<f', float(value)))[0]
    if int(struct.unpack('<f', struct.pack('<I', bits))[0]) != value:
        raise Trap  # inexact conversion: the hardware rounding mode for CVT.S.W is not established
    return bits


def _lwc1(s, f):
    s.fpr[f['rt']] = s.load(_ea(s, f), 4)


def _swc1(s, f):
    s.store(_ea(s, f), 4, s.fpr[f['rt']])


def _arith(op):
    """An FMAC-style result with its O/U flags: plain, or ACC (+/-) product for the multiply-add forms."""
    def value(s, a, b):
        if op in ('add', 'sub', 'mul'):
            result, overflow, underflow = _pff(op, a, b)
        elif _HOST_PRODUCT:
            result, overflow, underflow = _pff('add' if op in ('madd', 'madda') else 'sub', s.acc, _product(a, b))
        else:
            result, overflow, underflow = _pff(op, s.acc, a, b, 0)
        _fcr_arith(s, overflow, underflow)
        return result
    return value


def _fdiv(s, a, b):
    if b & 0x7F800000 == 0:  # a zero (or denormal) divisor: D, or I for 0/0
        s.fcr31 |= (FCR_I | FCR_SI) if a & 0x7F800000 == 0 else (FCR_D | FCR_SD)
    return _pf('div', a, b)


def _fsqrt(s, f):
    b = s.fpr[f['ft']]
    s.fcr31 &= ~(FCR_I | FCR_D)
    if b & 0x7F800000 and b >> 31:
        s.fcr31 |= FCR_I | FCR_SI  # root of a negative number
    s.fpr[f['fd']] = _pf('sqrt', b)


def _frsqrt(s, a, b):
    s.fcr31 &= ~(FCR_I | FCR_D)
    if b & 0x7F800000 == 0:
        s.fcr31 |= FCR_D | FCR_SD
    elif b >> 31:
        s.fcr31 |= FCR_I | FCR_SI
    return _pf('rsqrt', a, b)


def _clear_ou(value):
    def run(s, a, b):
        result = value(s, a, b)
        s.fcr31 &= ~(FCR_O | FCR_U)
        return result
    return run


FPU_FORMS = {
    'add.s': _cop1(0x10, 0x00, _fd(_arith('add'))),
    'sub.s': _cop1(0x10, 0x01, _fd(_arith('sub'))),
    'mul.s': _cop1(0x10, 0x02, _fd(_arith('mul'))),
    'div.s': _cop1(0x10, 0x03, _fd(_fdiv)),
    'sqrt.s': _cop1(0x10, 0x04, _fsqrt, unused=('fs',)),
    'abs.s': _cop1(0x10, 0x05, _fd(_clear_ou(lambda s, a, b: a & 0x7FFFFFFF)), unused=('ft',)),
    'mov.s': _cop1(0x10, 0x06, _fd(lambda s, a, b: a), unused=('ft',)),
    'neg.s': _cop1(0x10, 0x07, _fd(_clear_ou(lambda s, a, b: a ^ 0x80000000)), unused=('ft',)),
    'rsqrt.s': _cop1(0x10, 0x16, _fd(_frsqrt)),
    'adda.s': _cop1(0x10, 0x18, _to_acc(_arith('add')), unused=('fd',)),
    'suba.s': _cop1(0x10, 0x19, _to_acc(_arith('sub')), unused=('fd',)),
    'mula.s': _cop1(0x10, 0x1A, _to_acc(_arith('mul')), unused=('fd',)),
    'madd.s': _cop1(0x10, 0x1C, _fd(_arith('madd'))),
    'msub.s': _cop1(0x10, 0x1D, _fd(_arith('msub'))),
    'madda.s': _cop1(0x10, 0x1E, _to_acc(_arith('madda')), unused=('fd',)),
    'msuba.s': _cop1(0x10, 0x1F, _to_acc(_arith('msuba')), unused=('fd',)),
    'cvt.w.s': _cop1(0x10, 0x24, _fd(_cvt_w), unused=('ft',)),
    'max.s': _cop1(0x10, 0x28, _fd(_clear_ou(_pick(True)))),
    'min.s': _cop1(0x10, 0x29, _fd(_clear_ou(_pick(False)))),
    'c.f.s': _cop1(0x10, 0x30, _condition(None), unused=('fd',)),
    'c.eq.s': _cop1(0x10, 0x32, _condition(lambda o: o == 0), unused=('fd',)),
    'c.lt.s': _cop1(0x10, 0x34, _condition(lambda o: o < 0), unused=('fd',)),
    'c.le.s': _cop1(0x10, 0x36, _condition(lambda o: o <= 0), unused=('fd',)),
    'cvt.s.w': _cop1(0x14, 0x20, _fd(_cvt_s), unused=('ft',)),
    'mfc1': dict(layout='fgpr', encode=lambda f: (0x11 << 26) | (f['rt'] << 16) | (f['fs'] << 11),
                 run=lambda s, f: s.set(f['rt'], s32(s.fpr[f['fs']]))),
    'mtc1': dict(layout='fgpr', encode=lambda f: (0x11 << 26) | (4 << 21) | (f['rt'] << 16) | (f['fs'] << 11),
                 run=lambda s, f: s.fpr.__setitem__(f['fs'], s.get(f['rt']) & M32)),
    'lwc1': _mem(0x31, 4, _lwc1, 4),
    'swc1': _mem(0x39, 4, _swc1, 4),
}
FORMS.update(FPU_FORMS)

# Traps: only inputs whose condition is false are run (a true one raises an exception), so the
# check is that a trap that does not fire leaves every register untouched.
def _trap(condition):
    def run(s, f):
        if condition(s.get(f['rs']), s.get(f['rt'])):
            raise Trap
    return run


def _trap_imm(condition):
    def run(s, f):
        if condition(s.get(f['rs']), sext(f['imm'], 16) & M64):
            raise Trap
    return run


_ge = lambda a, b: signed64(a) >= signed64(b)
_lt = lambda a, b: signed64(a) < signed64(b)
TRAP_FORMS = {
    'tge': _special(0x30, _trap(_ge), 'trap'), 'tgeu': _special(0x31, _trap(lambda a, b: a >= b), 'trap'),
    'tlt': _special(0x32, _trap(_lt), 'trap'), 'tltu': _special(0x33, _trap(lambda a, b: a < b), 'trap'),
    'teq': _special(0x34, _trap(lambda a, b: a == b), 'trap'), 'tne': _special(0x36, _trap(lambda a, b: a != b), 'trap'),
}
for _name, _code, _condition_fn in (('tgei', 0x08, _ge), ('tgeiu', 0x09, lambda a, b: a >= b), ('tlti', 0x0A, _lt),
                                    ('tltiu', 0x0B, lambda a, b: a < b), ('teqi', 0x0C, lambda a, b: a == b),
                                    ('tnei', 0x0E, lambda a, b: a != b)):
    TRAP_FORMS[_name] = dict(layout='trapi', encode=lambda f, c=_code: i_type(0x01, f['rs'], c, f['imm']),
                             run=_trap_imm(_condition_fn))
FORMS.update(TRAP_FORMS)

# bc1f/bc1t and their likely forms, laid out like the integer branches; taken follows FCR31.C.
FPU_BRANCHES = {
    'bc1f': (0, False, False), 'bc1t': (1, True, False), 'bc1fl': (2, False, True), 'bc1tl': (3, True, True),
}

FLOATS = [0, 0x80000000, 0x3F800000, 0xBF800000, 0x7F7FFFFF, 0xFF7FFFFF, 0x7FFFFFFF, 0xFFFFFFFF, 0x7F800000,
          0xFF800000, 0x00000001, 0x00800000, 0x80800000, 0x007FFFFF, 0x4B000000, 0x4F000000, 0xCF000000, 0x40490FDB,
          0x00FFFFFF, 0x00800001, 0x3F000000, 0x3F7FFFFF, 0x7F000000, 0x80FFFFFF, 0x20000000, 0x5F000000]


def random_float(rng):
    roll = rng.random()
    if roll < 0.25:
        return rng.choice(FLOATS)
    if roll < 0.85:
        return (rng.getrandbits(1) << 31) | (rng.randrange(0x68, 0x98) << 23) | rng.getrandbits(23)
    return rng.getrandbits(32)


# ---- VU0 macro (COP2) forms ---------------------------------------------------------------------
# Lanes are x, y, z, w; the dest field's bit 3 selects x. VF0 reads (0, 0, 0, 1.0) and ignores writes,
# VI0 reads 0. Arithmetic goes through ps2float per lane; a multiply-add rounds the product, then
# the sum, as the FMAC does.
ONE = 0x3F800000


def _vf(s, r):
    return [0, 0, 0, ONE] if r == 0 else list(s.vf[r])


def _vf_write(s, r, values, dest):
    if r:
        for k in range(4):
            if dest & (8 >> k):
                s.vf[r][k] = values[k] & M32


def _acc_write(s, values, dest):
    for k in range(4):
        if dest & (8 >> k):
            s.vacc[k] = values[k] & M32


def _vi(s, r):
    return 0 if r == 0 else s.vi[r] & 0xFFFF


def _vi_write(s, r, value):
    if r:
        s.vi[r] = value & 0xFFFF


def _cop2(funct, fn, layout='vu'):
    def encode(f):
        return ((0x12 << 26) | (1 << 25) | (f['dest'] << 21) | (f['vt'] << 16) | (f['vs'] << 11) | (f['vd'] << 6)
                | funct)
    return dict(layout=layout, encode=encode, run=fn)


def _cop2x(opcode, fn, layout='vu'):
    """Special2 forms: the opcode's upper bits sit where vd would be."""
    def encode(f):
        return ((0x12 << 26) | (1 << 25) | (f['dest'] << 21) | (f['vt'] << 16) | (f['vs'] << 11)
                | ((opcode >> 2) << 6) | 0x3C | (opcode & 3))
    return dict(layout=layout, encode=encode, run=fn)


def _operand(kind):
    """The second operand vector: vt, a vt lane broadcast, Q or I."""
    if kind == 'vt':
        return lambda s, f: _vf(s, f['vt'])
    if kind == 'q':
        return lambda s, f: [s.q] * 4
    if kind == 'i':
        return lambda s, f: [s.i] * 4
    return lambda s, f: [_vf(s, f['vt'])[kind]] * 4


def _lanes(op, a, b, acc=None):
    """[(result, overflow, underflow)] per lane; a multiply-add reports its final add or subtract."""
    if op == 'madd':
        return [_pff('add', c, _product(x, y)) for c, x, y in zip(acc, a, b)]
    if op == 'msub':
        return [_pff('sub', c, _product(x, y)) for c, x, y in zip(acc, a, b)]
    if op in ('max', 'mini'):
        return [(_pick(op == 'max')(None, x, y), False, False) for x, y in zip(a, b)]
    return [_pff(op, x, y) for x, y in zip(a, b)]


MAC_FLAGS = 0xFFFF  # zero (bits 3..0), sign (7..4), underflow (11..8), overflow (15..12); x is the high bit
STATUS_FLAGS = 0xFFF  # Z S U O I D (bits 0..5) and their sticky copies (6..11)


def _mac(s, results, dest):
    """FMAC results set the MAC flags of the written lanes (clearing the others), even when the
    destination is VF0 (a flag-only compare), and the status summary with its sticky copies."""
    mac = 0
    for k, (value, overflow, underflow) in enumerate(results):
        if dest & (8 >> k):
            bit = 3 - k
            mac |= (value & 0x7F800000 == 0) << bit | (value >> 31) << (4 + bit) | underflow << (8 + bit) \
                | overflow << (12 + bit)
    s.mac = mac
    summary = sum(1 << n for n in range(4) if mac & (0xF << (4 * n)))
    s.status = (s.status & ~0xF) | summary | summary << 6


def _status_divide(s, invalid, divide):
    s.status = (s.status & ~0x30) | (0x10 | 0x400 if invalid else 0) | (0x20 | 0x800 if divide else 0) | (s.status & 0xC00)


def _varith(op, kind, to_acc=False):
    second = _operand(kind)

    def run(s, f):
        results = _lanes(op, _vf(s, f['vs']), second(s, f), s.vacc)
        values = [r[0] for r in results]
        if to_acc:
            _acc_write(s, values, f['dest'])
        else:
            _vf_write(s, f['vd'], values, f['dest'])
        if op not in ('max', 'mini'):
            _mac(s, results, f['dest'])
    return run


def _outer(to_acc):
    # OPMULA: ACC.xyz = vs.yzx * vt.zxy; OPMSUB: vd.xyz = ACC - vs.yzx * vt.zxy.
    def run(s, f):
        a, b = _vf(s, f['vs']), _vf(s, f['vt'])
        a, b = [a[1], a[2], a[0], a[3]], [b[2], b[0], b[1], b[3]]
        results = _lanes('mul', a, b) if to_acc else _lanes('msub', a, b, s.vacc)
        values = [r[0] for r in results]
        if to_acc:
            _acc_write(s, values, f['dest'] & 0xE)
        else:
            _vf_write(s, f['vd'], values, f['dest'] & 0xE)
        _mac(s, results, f['dest'] & 0xE)
    return run


def _itof(shift):
    def lane(bits):
        value = sext(bits, 32)
        if value == 0:
            return 0
        single = struct.unpack('<I', struct.pack('<f', float(value)))[0]
        if int(struct.unpack('<f', struct.pack('<I', single))[0]) != value:
            raise Trap  # inexact: the hardware rounding mode for ITOF is not established
        return struct.unpack('<I', struct.pack('<f', value / (1 << shift)))[0]
    return lambda s, f: _vf_write(s, f['vt'], [lane(x) for x in _vf(s, f['vs'])], f['dest'])


def _ftoi(shift):
    def lane(bits):
        exponent = (bits >> 23) & 0xFF
        if exponent == 0:
            return 0
        mantissa, scale = (1 << 23) | (bits & 0x7FFFFF), exponent - 150 + shift
        magnitude = mantissa << scale if scale >= 0 else mantissa >> -scale
        value = -magnitude if bits >> 31 else magnitude
        return max(-(1 << 31), min((1 << 31) - 1, value)) & M32
    return lambda s, f: _vf_write(s, f['vt'], [lane(x) for x in _vf(s, f['vs'])], f['dest'])


def _clipw(s, f):
    v, w = _vf(s, f['vs']), _vf(s, f['vt'])[3]
    lib = ps2float()
    judgement = 0
    for k in range(3):
        judgement |= lib.ps2f_clip(v[k], w) << (2 * k)
    s.clip = ((s.clip << 6) | judgement) & 0xFFFFFF


def _field(f, key):
    return (f['dest'] >> (0 if key == 'fsf' else 2)) & 3  # fsf: bits 22:21, ftf: bits 24:23


def _zero(bits):
    return bits & 0x7F800000 == 0


def _vdiv(s, f):
    a, b = _vf(s, f['vs'])[_field(f, 'fsf')], _vf(s, f['vt'])[_field(f, 'ftf')]
    s.q = _pf('div', a, b)
    _status_divide(s, _zero(b) and _zero(a), _zero(b) and not _zero(a))  # I for 0/0, D for x/0


def _vsqrt(s, f):
    b = _vf(s, f['vt'])[_field(f, 'ftf')]
    s.q = _pf('sqrt', b)
    _status_divide(s, not _zero(b) and b >> 31, False)  # I for a negative root


def _vrsqrt(s, f):
    a, b = _vf(s, f['vs'])[_field(f, 'fsf')], _vf(s, f['vt'])[_field(f, 'ftf')]
    s.q = _pf('rsqrt', a, b)
    _status_divide(s, (_zero(b) and _zero(a)) or (not _zero(b) and b >> 31), _zero(b))


def _lqc2(s, f):
    a = _ea(s, f)
    _vf_write(s, f['rt'], [s.load(a + 4 * k, 4) for k in range(4)], 0xF)


def _sqc2(s, f):
    a = _ea(s, f)
    for k, value in enumerate(_vf(s, f['rt'])):
        s.store(a + 4 * k, 4, value)


def _cop2_move(rs, fn):
    return dict(layout='vgpr', encode=lambda f: (0x12 << 26) | (rs << 21) | (f['rt'] << 16) | (f['vs'] << 11), run=fn)


def _qmtc2(s, f):
    low, high = (0, 0) if f['rt'] == 0 else s.gpr[f['rt']]
    _vf_write(s, f['vs'], [low & M32, low >> 32, high & M32, high >> 32], 0xF)


def _qmfc2(s, f):
    v = _vf(s, f['vs'])
    s.set128(f['rt'], v[0] | (v[1] << 32), v[2] | (v[3] << 32))


VU_FORMS = {}
for _base, _funct, _op in (('vadd', 0x00, 'add'), ('vsub', 0x04, 'sub'), ('vmadd', 0x08, 'madd'),
                           ('vmsub', 0x0C, 'msub'), ('vmax', 0x10, 'max'), ('vmini', 0x14, 'mini'),
                           ('vmul', 0x18, 'mul')):
    for _lane in range(4):
        VU_FORMS[_base + 'xyzw'[_lane]] = _cop2(_funct + _lane, _varith(_op, _lane))
for _base, _opcode, _op in (('vadda', 0x00, 'add'), ('vsuba', 0x04, 'sub'), ('vmadda', 0x08, 'madd'),
                            ('vmsuba', 0x0C, 'msub'), ('vmula', 0x18, 'mul')):
    for _lane in range(4):
        VU_FORMS[_base + 'xyzw'[_lane]] = _cop2x(_opcode + _lane, _varith(_op, _lane, True))
for _name, _funct, _op, _kind in (('vmulq', 0x1C, 'mul', 'q'), ('vmaxi', 0x1D, 'max', 'i'), ('vmuli', 0x1E, 'mul', 'i'),
                                  ('vminii', 0x1F, 'mini', 'i'), ('vaddq', 0x20, 'add', 'q'), ('vmaddq', 0x21, 'madd', 'q'),
                                  ('vaddi', 0x22, 'add', 'i'), ('vmaddi', 0x23, 'madd', 'i'), ('vsubq', 0x24, 'sub', 'q'),
                                  ('vmsubq', 0x25, 'msub', 'q'), ('vsubi', 0x26, 'sub', 'i'), ('vmsubi', 0x27, 'msub', 'i'),
                                  ('vadd', 0x28, 'add', 'vt'), ('vmadd', 0x29, 'madd', 'vt'), ('vmul', 0x2A, 'mul', 'vt'),
                                  ('vmax', 0x2B, 'max', 'vt'), ('vsub', 0x2C, 'sub', 'vt'), ('vmsub', 0x2D, 'msub', 'vt'),
                                  ('vmini', 0x2F, 'mini', 'vt')):
    VU_FORMS[_name] = _cop2(_funct, _varith(_op, _kind))
for _name, _opcode, _op, _kind in (('vmulaq', 0x1C, 'mul', 'q'), ('vmulai', 0x1E, 'mul', 'i'), ('vaddaq', 0x20, 'add', 'q'),
                                   ('vmaddaq', 0x21, 'madd', 'q'), ('vaddai', 0x22, 'add', 'i'), ('vmaddai', 0x23, 'madd', 'i'),
                                   ('vsubaq', 0x24, 'sub', 'q'), ('vmsubaq', 0x25, 'msub', 'q'), ('vsubai', 0x26, 'sub', 'i'),
                                   ('vmsubai', 0x27, 'msub', 'i'), ('vadda', 0x28, 'add', 'vt'), ('vmadda', 0x29, 'madd', 'vt'),
                                   ('vmula', 0x2A, 'mul', 'vt'), ('vsuba', 0x2C, 'sub', 'vt'), ('vmsuba', 0x2D, 'msub', 'vt')):
    VU_FORMS[_name] = _cop2x(_opcode, _varith(_op, _kind, True))
for _shift, _opcode in ((0, 0x10), (4, 0x11), (12, 0x12), (15, 0x13)):
    VU_FORMS[f'vitof{_shift}'] = _cop2x(_opcode, _itof(_shift))
    VU_FORMS[f'vftoi{_shift}'] = _cop2x(_opcode + 4, _ftoi(_shift))
VU_FORMS.update({
    'vopmsub': _cop2(0x2E, _outer(False)),
    'vopmula': _cop2x(0x2E, _outer(True)),
    'viadd': _cop2(0x30, lambda s, f: _vi_write(s, f['vd'], _vi(s, f['vs']) + _vi(s, f['vt']))),
    'visub': _cop2(0x31, lambda s, f: _vi_write(s, f['vd'], _vi(s, f['vs']) - _vi(s, f['vt']))),
    'viaddi': _cop2(0x32, lambda s, f: _vi_write(s, f['vt'], _vi(s, f['vs']) + sext(f['vd'], 5))),
    'viand': _cop2(0x34, lambda s, f: _vi_write(s, f['vd'], _vi(s, f['vs']) & _vi(s, f['vt']))),
    'vior': _cop2(0x35, lambda s, f: _vi_write(s, f['vd'], _vi(s, f['vs']) | _vi(s, f['vt']))),
    'vabs': _cop2x(0x1D, lambda s, f: _vf_write(s, f['vt'], [x & 0x7FFFFFFF for x in _vf(s, f['vs'])], f['dest'])),
    'vclipw': _cop2x(0x1F, _clipw),
    'vnop': _cop2x(0x2F, lambda s, f: None),
    'vmove': _cop2x(0x30, lambda s, f: _vf_write(s, f['vt'], _vf(s, f['vs']), f['dest'])),
    'vmr32': _cop2x(0x31, lambda s, f: _vf_write(s, f['vt'], _vf(s, f['vs'])[1:] + _vf(s, f['vs'])[:1], f['dest'])),
    'vdiv': _cop2x(0x38, _vdiv), 'vsqrt': _cop2x(0x39, _vsqrt), 'vrsqrt': _cop2x(0x3A, _vrsqrt),
    'vwaitq': _cop2x(0x3B, lambda s, f: None),
    'vmtir': _cop2x(0x3C, lambda s, f: _vi_write(s, f['vt'], _vf(s, f['vs'])[_field(f, 'fsf')])),
    'vmfir': _cop2x(0x3D, lambda s, f: _vf_write(s, f['vt'], [s32(sext(_vi(s, f['vs']), 16)) & M32] * 4, f['dest'])),
    'qmfc2': _cop2_move(1, _qmfc2),
    'qmtc2': _cop2_move(5, _qmtc2),
    'cfc2': _cop2_move(2, lambda s, f: s.set(f['rt'], _vi(s, f['vs'] & 15))),
    'ctc2': _cop2_move(6, lambda s, f: _vi_write(s, f['vs'] & 15, s.get(f['rt']))),
    'lqc2': _mem(0x36, 16, _lqc2, 16),
    'sqc2': _mem(0x3E, 16, _sqc2, 16),
})
FORMS.update(VU_FORMS)


# ---- VU0 data memory forms (VLQI/VSQI/VLQD/VSQD, VILWR/VISWR) -------------------------------------
# Quadword addresses come from a VI register and wrap in VU0's 4 KB data memory.
def _vu_addr(s, r, lane=0):
    return ((_vi(s, r) & getattr(s, 'vumask', 0xFF)) << 4) + 4 * lane  # VU1 microprograms: 0x3FF


def _vu_load(s, address):
    return int.from_bytes(s.vumem[address:address + 4], 'little')


def _vu_store(s, address, value):
    s.vumem[address:address + 4] = (value & M32).to_bytes(4, 'little')


def _vlq(step):
    def run(s, f):
        r = f['vs']  # is
        if step < 0:
            _vi_write(s, r, _vi(s, r) - 1)
        base = _vu_addr(s, r)
        _vf_write(s, f['vt'], [_vu_load(s, base + 4 * k) for k in range(4)], f['dest'])
        if step > 0:
            _vi_write(s, r, _vi(s, r) + 1)
    return run


def _vsq(step):
    def run(s, f):
        r = f['vt']  # it
        if step < 0:
            _vi_write(s, r, _vi(s, r) - 1)
        base, v = _vu_addr(s, r), _vf(s, f['vs'])
        for k in range(4):
            if f['dest'] & (8 >> k):
                _vu_store(s, base + 4 * k, v[k])
        if step > 0:
            _vi_write(s, r, _vi(s, r) + 1)
    return run


def _lane_of(dest):
    return {8: 0, 4: 1, 2: 2, 1: 3}[dest]


VU_MEMORY_FORMS = {
    'vlqi': _cop2x(0x34, _vlq(1)), 'vsqi': _cop2x(0x35, _vsq(1)),
    'vlqd': _cop2x(0x36, _vlq(-1)), 'vsqd': _cop2x(0x37, _vsq(-1)),
    'vilwr': _cop2x(0x3E, lambda s, f: _vi_write(s, f['vt'], _vu_load(s, _vu_addr(s, f['vs'], _lane_of(f['dest']))))),
    'viswr': _cop2x(0x3F, lambda s, f: _vu_store(s, _vu_addr(s, f['vs'], _lane_of(f['dest'])), _vi(s, f['vt']))),
}
VU_FORMS.update(VU_MEMORY_FORMS)
FORMS.update(VU_MEMORY_FORMS)


# ---- calls and link branches ----------------------------------------------------------------------
# Each case saves $ra in $26, makes the call or link branch, copies $ra to $10 and returns through $26.
# The harness's runtime stand-in records a call's target in $11 and the $ra the callee sees in $12.
# Case start addresses are added to the fields by check(), since link values depend on them.
SAVE_RA, LINK_COPY, CALL_TARGET_REG = 26, 10, 13
CALL_REGISTERS = {8, 9, SAVE_RA, LINK_COPY, 11, 12, CALL_TARGET_REG}
RETURN_TO = 0x1000  # the harness's return address
EXTERNAL_CALL = 0x00180000  # outside the case program


def _daddu(rd, rs):
    return r_type(0x2D, rs, 0, rd)


def _jalr(rd, rs):
    return r_type(0x09, rs, 0, rd)


_ADDIU_TARGET = i_type(0x09, CALL_TARGET_REG, CALL_TARGET_REG, 4)
CALL_LAYOUTS = {
    # name: (words, link register or None, delay slot writes the target register)
    'jal': lambda f: [_daddu(SAVE_RA, RA), (0x03 << 26) | (EXTERNAL_CALL >> 2), _addiu(BRANCH_TAKEN_REG),
                      _daddu(LINK_COPY, RA), r_type(0x08, SAVE_RA), NOP],
    'jalr': lambda f: [_daddu(SAVE_RA, RA), _jalr(LINK_COPY, CALL_TARGET_REG), _addiu(BRANCH_TAKEN_REG),
                       r_type(0x08, SAVE_RA), NOP],
    'jalr.ra': lambda f: [_daddu(SAVE_RA, RA), _jalr(RA, CALL_TARGET_REG), _addiu(BRANCH_TAKEN_REG),
                          _daddu(LINK_COPY, RA), r_type(0x08, SAVE_RA), NOP],
    'jalr.alias': lambda f: [_daddu(SAVE_RA, RA), _jalr(CALL_TARGET_REG, CALL_TARGET_REG), _addiu(BRANCH_TAKEN_REG),
                             r_type(0x08, SAVE_RA), NOP],
    'jalr.delay-writes-target': lambda f: [_daddu(SAVE_RA, RA), _jalr(LINK_COPY, CALL_TARGET_REG), _ADDIU_TARGET,
                                           r_type(0x08, SAVE_RA), NOP],
}
LINK_BRANCHES = {'bltzal': (0x10, lambda v: signed64(v) < 0, False), 'bgezal': (0x11, lambda v: signed64(v) >= 0, False),
                 'bltzall': (0x12, lambda v: signed64(v) < 0, True), 'bgezall': (0x13, lambda v: signed64(v) >= 0, True)}
for _name, (_code, _, _) in LINK_BRANCHES.items():
    CALL_LAYOUTS[_name] = (lambda code: lambda f: [_daddu(SAVE_RA, RA), i_type(0x01, f['rs'], code, 2),
                                                   _addiu(BRANCH_TAKEN_REG), _addiu(BRANCH_SKIPPED_REG),
                                                   _daddu(LINK_COPY, RA), r_type(0x08, SAVE_RA), NOP])(_code)


def _set_low(s, r, value):
    s.gpr[r][0] = value & M64


def _call_run(name):
    def run(s, f):
        link = s32(f['start'] + 12)
        _set_low(s, SAVE_RA, RETURN_TO)
        s.gpr[RA] = [RETURN_TO, 0]
        if name in LINK_BRANCHES:
            _, condition, likely = LINK_BRANCHES[name]
            taken = condition(s.get(f['rs']))
            _set_low(s, RA, link)  # the link is written whether or not the branch is taken
            if taken or not likely:
                s.set(BRANCH_TAKEN_REG, s32(s.get(BRANCH_TAKEN_REG) + 1))
            if not taken:
                s.set(BRANCH_SKIPPED_REG, s32(s.get(BRANCH_SKIPPED_REG) + 1))
            _set_low(s, LINK_COPY, s.gpr[RA][0])
            return
        if name == 'jal':
            target = EXTERNAL_CALL
            _set_low(s, RA, link)
        else:
            target = s.get(CALL_TARGET_REG) & M32  # read before the link is written
            _set_low(s, {'jalr.ra': RA, 'jalr.alias': CALL_TARGET_REG}.get(name, LINK_COPY), link)
        if name == 'jalr.delay-writes-target':
            s.set(CALL_TARGET_REG, s32(s.get(CALL_TARGET_REG) + 4))
        else:
            s.set(BRANCH_TAKEN_REG, s32(s.get(BRANCH_TAKEN_REG) + 1))
        s.set128(11, target, 0)
        s.set128(12, s.gpr[RA][0], 0)
        if name in ('jal', 'jalr.ra'):
            _set_low(s, LINK_COPY, s.gpr[RA][0])
    return run

# Conditional branches: `b rs, rt, +2; addiu r8, r8, 1 (delay); addiu r9, r9, 1; jr ra; nop`.
# Taken skips the r9 increment; a likely branch also nullifies the delay slot when not taken.
BRANCHES = {
    'beq': (lambda rs, rt: i_type(0x04, rs, rt, 2), lambda a, b: a == b, False),
    'bne': (lambda rs, rt: i_type(0x05, rs, rt, 2), lambda a, b: a != b, False),
    'blez': (lambda rs, rt: i_type(0x06, rs, 0, 2), lambda a, b: signed64(a) <= 0, False),
    'bgtz': (lambda rs, rt: i_type(0x07, rs, 0, 2), lambda a, b: signed64(a) > 0, False),
    'bltz': (lambda rs, rt: i_type(0x01, rs, 0x00, 2), lambda a, b: signed64(a) < 0, False),
    'bgez': (lambda rs, rt: i_type(0x01, rs, 0x01, 2), lambda a, b: signed64(a) >= 0, False),
    'beql': (lambda rs, rt: i_type(0x14, rs, rt, 2), lambda a, b: a == b, True),
    'bnel': (lambda rs, rt: i_type(0x15, rs, rt, 2), lambda a, b: a != b, True),
    'blezl': (lambda rs, rt: i_type(0x16, rs, 0, 2), lambda a, b: signed64(a) <= 0, True),
    'bgtzl': (lambda rs, rt: i_type(0x17, rs, 0, 2), lambda a, b: signed64(a) > 0, True),
    'bltzl': (lambda rs, rt: i_type(0x01, rs, 0x02, 2), lambda a, b: signed64(a) < 0, True),
    'bgezl': (lambda rs, rt: i_type(0x01, rs, 0x03, 2), lambda a, b: signed64(a) >= 0, True),
}


def _addiu(reg):
    return i_type(0x09, reg, reg, 1)


def _branch_run(condition, likely, flag=False):
    def run(s, f):
        taken = condition(s) if flag else condition(s.get(f['rs']), s.get(f['rt']))
        if taken or not likely:
            s.set(BRANCH_TAKEN_REG, s32(s.get(BRANCH_TAKEN_REG) + 1))
        if not taken:
            s.set(BRANCH_SKIPPED_REG, s32(s.get(BRANCH_SKIPPED_REG) + 1))
    return run


# ---- cases ------------------------------------------------------------------------------------
USABLE = [r for r in range(1, 31) if r not in (BRANCH_TAKEN_REG, BRANCH_SKIPPED_REG)]


def cases(rng, variants=3):
    """(name, words, fields) for every form, `variants` register/immediate choices each."""
    out = []
    for name, form in FORMS.items():
        for v in range(variants):
            rs, rt, rd = rng.sample(USABLE, 3)
            if v == 1 and form['layout'] not in ('trap',):
                rd = rt = rs  # aliasing: source and destination are one register
            if v == 2 and form['layout'] not in ('mem',):
                rd = 0  # writes to $zero are discarded
                if form['layout'] == 'imm':
                    rt = 0
            fields = dict(rs=rs, rt=rt, rd=rd, sa=rng.randrange(32), imm=rng.randrange(1 << 16))
            fs, ft, fd = rng.sample(range(32), 3)
            fields.update(fs=fs, ft=ft, fd=fd)
            if v == 1:
                fields.update(ft=fs, fd=fs)  # FPU aliasing: one register is source and destination
            vs, vt, vd = rng.sample(range(1, 32), 3)
            fields.update(vs=vs, vt=vt, vd=vd, dest=rng.randrange(1, 16))
            if v == 1:
                fields.update(vt=vs, vd=vs)
            if v == 2:
                fields.update(vt=0, vd=0)  # VF0 is read-only: (0, 0, 0, 1.0)
                if form['layout'] == 'vgpr' and name not in ('cfc2', 'ctc2'):
                    fields['vs'] = 0  # QMTC2 into VF0 is ignored, QMFC2 from it reads the constant
            if name in ('vilwr', 'viswr'):
                fields['dest'] = rng.choice([8, 4, 2, 1])  # one lane
            if name in ('vopmula', 'vopmsub', 'vclipw'):
                fields['dest'] = 0xE  # the assembler always encodes xyz
            if name in ('viadd', 'visub', 'viand', 'vior', 'viaddi', 'vmtir', 'vmfir', 'cfc2', 'ctc2',
                        'vlqi', 'vsqi', 'vlqd', 'vsqd', 'vilwr', 'viswr'):
                fields.update(vs=fields['vs'] & 15, vt=fields['vt'] & 15)  # VI0-VI15
                if name != 'viaddi':
                    fields['vd'] &= 15
            if form['layout'] == 'mem':
                if v == 1:
                    rt = rs = rng.choice(USABLE)  # base register overwritten by the load
                fields.update(rs=rs, rt=rt, imm=sext(rng.randrange(-64, 64), 16) & 0xFFFF)
                if name == 'lqc2' and v == 2:
                    fields['rt'] = 0  # LQC2 into VF0 is ignored
            out.append((name, [form['encode'](fields), JR_RA, NOP], fields))
    for name, (setter, _) in SA_SEQUENCES.items():
        for v in range(variants):
            rs, rt, rd = rng.sample(USABLE, 3)
            if v == 1:
                rd = rt = rs
            fields = dict(rs=rs, rt=rt, rd=0 if v == 2 else rd, imm=rng.randrange(1 << 16))
            out.append((name, _qfsrv_words(setter, fields) + [JR_RA, NOP], fields))
    for name, layout in CALL_LAYOUTS.items():
        for v in range(variants):
            fields = dict(rs=rng.choice([r for r in USABLE if r not in CALL_REGISTERS]))
            out.append((name, layout(fields), fields))
    for name, (code, _, _) in FPU_BRANCHES.items():
        word = (0x11 << 26) | (8 << 21) | (code << 16) | 2
        out.append((name, [word, _addiu(BRANCH_TAKEN_REG), _addiu(BRANCH_SKIPPED_REG), JR_RA, NOP], {}))
    for name, (encode, condition, likely) in BRANCHES.items():
        for v in range(variants):
            rs, rt = rng.sample(USABLE, 2)
            if v == 1:
                rt = rs
            fields = dict(rs=rs, rt=rt)
            out.append((name, [encode(rs, rt), _addiu(BRANCH_TAKEN_REG), _addiu(BRANCH_SKIPPED_REG), JR_RA, NOP], fields))
    return out


def _model(name):
    if name in FORMS:
        return FORMS[name]['run']
    if name in SA_SEQUENCES:
        return _qfsrv_run(SA_SEQUENCES[name][1])
    if name in CALL_LAYOUTS:
        return _call_run(name)
    if name in FPU_BRANCHES:
        _, want, likely = FPU_BRANCHES[name]
        return _branch_run(lambda s: bool(s.fcr31 & FCR31_C) == want, likely, flag=True)
    _, condition, likely = BRANCHES[name]
    return _branch_run(condition, likely)


INTERESTING = [0, 1, M64, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF80000000, 0xFFFFFFFF, 0x8000000000000000,
               0x7FFFFFFFFFFFFFFF, 0x100000000, 0xFFFFFFFE]


def random_state(rng, name, fields):
    def value():
        return rng.choice(INTERESTING) if rng.random() < 0.3 else rng.getrandbits(64)
    gpr = [[value(), rng.getrandbits(64)] for _ in range(32)]
    gpr[0] = [0, 0]
    gpr[RA] = [0, 0]  # the harness sets the return address
    state = State(gpr, value(), value(), value(), value(), bytes(rng.getrandbits(8) for _ in range(DATA_BYTES)),
                  [random_float(rng) for _ in range(32)], random_float(rng), rng.getrandbits(32) & FCR31_FLAGS)
    state.vf = [[0, 0, 0, ONE]] + [[random_float(rng) for _ in range(4)] for _ in range(31)]
    state.vacc = [random_float(rng) for _ in range(4)]
    state.q, state.i, state.clip, state.mac = random_float(rng), random_float(rng), rng.getrandbits(24), rng.getrandbits(16)
    state.status = rng.getrandbits(12)
    state.vi = [0] + [rng.getrandbits(16) for _ in range(15)]
    state.vumem = bytearray(rng.getrandbits(8) for _ in range(VU0_DATA_BYTES))
    if name in CALL_LAYOUTS:
        state.gpr[CALL_TARGET_REG][0] = rng.choice([0x00180000, 0x00200040, rng.getrandbits(32) & ~3]) | \
            (rng.getrandbits(32) << 32)  # only the low word is the target
        if name in LINK_BRANCHES and rng.random() < 0.3:
            state.gpr[fields['rs']][0] = rng.choice([0, M64, 1 << 63, 0x80000000])
    if name in VU_FORMS and fields.get('vt') and rng.random() < 0.2:
        state.vf[fields['vt']] = list(state.vf[fields['vs']])  # equal operands for MAX/MINI (VF0 stays constant)
    if name in ('cvt.s.w',) and rng.random() < 0.8:
        state.fpr[fields['fs']] = rng.randrange(-(1 << 24), 1 << 24) & M32  # integers the conversion keeps exact
    if name.startswith('vitof'):
        state.vf[fields['vs']] = [rng.randrange(-(1 << 24), 1 << 24) & M32 if rng.random() < 0.9 else rng.getrandbits(32)
                                  for _ in range(4)] if fields['vs'] else state.vf[0]
    if name in TRAP_FORMS and FORMS[name]['layout'] == 'trapi' and rng.random() < 0.5:
        state.gpr[fields['rs']][0] = (sext(fields['imm'], 16) + rng.randrange(-4, 5)) & M64  # near the bound
    if name in ('tne', 'tnei') and rng.random() < 0.8:
        # equal operands, so the inequality traps get inputs that do not fire
        other = sext(fields['imm'], 16) & M64 if FORMS[name]['layout'] == 'trapi' else state.gpr[fields['rt']][0]
        state.gpr[fields['rs']][0] = other
    if name in FPU_FORMS and rng.random() < 0.2:
        state.fpr[fields['ft']] = state.fpr[fields['fs']]  # equal operands for the compares
    form = FORMS.get(name)
    if name in ('div', 'divu', 'div1', 'divu1') and rng.random() < 0.25:
        state.gpr[fields['rt']][0] = rng.choice([0, M64])  # divide by zero, and by -1 for INT_MIN / -1
        state.gpr[fields['rs']][0] = rng.choice([0xFFFFFFFF80000000, value()])
    if name in ('pdivw', 'pdivuw', 'pdivbw') and rng.random() < 0.25:
        state.gpr[fields['rt']][0] &= ~M32 if name != 'pdivbw' else ~0xFFFF  # a zero divisor lane
    if name in MMI_FORMS and rng.random() < 0.3:
        # equal lanes for the compare forms, near-limit lanes for the saturating ones
        rs, rt = fields['rs'], fields['rt']
        for half in (0, 1):
            if rng.random() < 0.5:
                state.gpr[rt][half] = state.gpr[rs][half]
            else:
                state.gpr[rt][half] = rng.choice([0x7FFF7FFF7FFF7FFF, 0x8000800080008000, 0x7F7F7F7F80808080,
                                                  0xFFFFFFFFFFFFFFFF, 0x7FFFFFFF80000000])
    if name in BRANCHES and rng.random() < 0.3:
        state.gpr[fields['rt']][0] = state.gpr[fields['rs']][0]  # equal operands
    if form and form['layout'] == 'mem':
        size, align = form['size'], form['align']
        offset = sext(fields['imm'], 16)
        span = 16 if size == 16 else size
        while True:
            address = DATA_BASE + rng.randrange(0, DATA_BYTES - span + 1)
            address -= address % align
            if size == 16:
                address &= ~15
            base = address - offset
            if DATA_BASE <= address and address + span <= DATA_BASE + DATA_BYTES:
                break
        state.gpr[fields['rs']][0] = base & M64
        # Left/right forms touch bytes on one side of the address only; keep both sides in the window.
        if name in ('lwl', 'lwr', 'swl', 'swr', 'ldl', 'ldr', 'sdl', 'sdr'):
            width = 4 if name[1] == 'w' else 8
            address = DATA_BASE + width + rng.randrange(DATA_BYTES - 2 * width)
            state.gpr[fields['rs']][0] = (address - offset) & M64
    return state


def expected(name, fields, state):
    out = state.copy()
    _model(name)(out, fields)
    return out


# ---- translation and harness ---------------------------------------------------------------------
STATE_FORMAT = '<' + 'Q' * (32 * 2 + 4) + 'I' * (32 + 2) + 'I' * VU_WORDS + f'{DATA_BYTES}s{VU0_DATA_BYTES}s'


def pack(state):
    flat = [word for reg in state.gpr for word in reg]
    return struct.pack(STATE_FORMAT, *flat, state.hi, state.lo, state.hi1, state.lo1, *state.fpr, state.acc,
                       state.fcr31, *state.vu_words(), bytes(state.mem), bytes(state.vumem))


def unpack(raw):
    values = struct.unpack(STATE_FORMAT, raw)
    gpr = [[values[2 * i], values[2 * i + 1]] for i in range(32)]
    return State(gpr, *values[64:68], values[-2], values[68:100], values[100], values[101], values[102:-2], values[-1])


def program(case_list):
    """Code words with an entry function that calls every case, and each case's address."""
    entry_words = 2 * len(case_list) + 2
    address = CODE_BASE + 4 * entry_words
    starts, body = [], []
    for _, words, _ in case_list:
        starts.append(address)
        body += words
        address += 4 * len(words)
    entry = []
    for start in starts:
        entry += [(0x03 << 26) | ((start >> 2) & 0x3FFFFFF), NOP]
    entry += [JR_RA, NOP]
    words = entry + body
    return b''.join(struct.pack('<I', w) for w in words), starts


HARNESS = r'''
#include "ps2_runtime_macros.h"
#include "ps2_runtime.h"
#include <ps2_recompiled_functions.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
using Fn = void (*)(uint8_t *, R5900Context *, PS2Runtime *);
static const Fn kCases[] = {@FUNCTIONS@};
static const uint32_t kStarts[] = {@STARTS@};

// Runtime stand-ins. A call records its target in $11 and the $ra it sees in $12, then resumes at the
// fall-through (as a callee that returns at once); a jump leaves the function. VU0 data memory is a
// local buffer, reached through the runtime's inline accessors.
bool PS2Runtime::dispatchGuestBranch(uint8_t *, R5900Context *ctx, uint32_t targetPc, uint32_t, uint32_t fallthroughPc,
                                     GuestBranchKind kind, const char *) {
    if (kind == GuestBranchKind::DirectCall || kind == GuestBranchKind::IndirectCall) {
        ctx->r[11] = _mm_set_epi64x(0, (int64_t)(uint64_t)targetPc);
        ctx->r[12] = _mm_set_epi64x(0, (int64_t)PS2_EXTRACT_EPI64_0(ctx->r[31]));
        ctx->pc = fallthroughPc;
        return true;
    }
    ctx->pc = targetPc;
    return false;
}
struct Vu0DataTag { using type = uint8_t *PS2Memory::*; friend type stolen(Vu0DataTag); };
template <typename Tag, typename Tag::type Member> struct Steal { friend typename Tag::type stolen(Tag) { return Member; } };
template struct Steal<Vu0DataTag, &PS2Memory::m_vu0Data>;
alignas(PS2Runtime) static unsigned char g_runtime[sizeof(PS2Runtime)];
static uint8_t g_vu0Data[@VU0@];

int main(int argc, char **argv) {
    PS2Runtime *runtime = reinterpret_cast<PS2Runtime *>(g_runtime);
    runtime->memory().*stolen(Vu0DataTag{}) = g_vu0Data;
    // input records: uint32 case, then the state; output: the state after the function returns
    auto rdram = std::make_unique<uint8_t[]>(PS2_RAM_SIZE);
    std::memset(rdram.get(), 0, PS2_RAM_SIZE);
    auto ctx = std::make_unique<R5900Context>();
    uint64_t words[68];
    uint32_t fpu[34];  // f0..f31, ACC, FCR31
    uint32_t vu[153];  // VF0..VF31, ACC, Q, I, clip flags, MAC flags, status, VI0..VI15
    uint8_t mem[@DATA@];
    uint32_t index;
    while (std::fread(&index, 4, 1, stdin) == 1) {
        if (std::fread(words, 8, 68, stdin) != 68 || std::fread(fpu, 4, 34, stdin) != 34 ||
            std::fread(vu, 4, 153, stdin) != 153 ||
            std::fread(mem, 1, sizeof(mem), stdin) != sizeof(mem) ||
            std::fread(g_vu0Data, 1, sizeof(g_vu0Data), stdin) != sizeof(g_vu0Data)) return 2;
        if (index >= sizeof(kCases) / sizeof(kCases[0])) return 3;
        std::memset(ctx.get(), 0, sizeof(R5900Context));
        for (int r = 0; r < 32; ++r) ctx->r[r] = _mm_set_epi64x((int64_t)words[2 * r + 1], (int64_t)words[2 * r]);
        ctx->hi = words[64]; ctx->lo = words[65]; ctx->hi1 = words[66]; ctx->lo1 = words[67];
        std::memcpy(ctx->f, fpu, 128); std::memcpy(&ctx->f_acc, fpu + 32, 4); ctx->fcr31 = fpu[33];
        std::memcpy(ctx->vu0_vf, vu, 512); std::memcpy(&ctx->vu0_acc, vu + 128, 16);
        std::memcpy(&ctx->vu0_q, vu + 132, 4); std::memcpy(&ctx->vu0_i, vu + 133, 4); ctx->vu0_clip_flags = vu[134];
        ctx->vu0_mac_flags = vu[135]; ctx->vu0_status = (uint16_t)vu[136];
        for (int k = 0; k < 16; ++k) ctx->vi[k] = (uint16_t)vu[137 + k];
        std::memcpy(rdram.get() + @DATA_BASE@u, mem, sizeof(mem));
        const uint32_t returnTo = 0x00001000u;
        ctx->r[31] = _mm_set_epi64x(0, returnTo);
        ctx->pc = kStarts[index];
        kCases[index](rdram.get(), ctx.get(), runtime);
        if (ctx->pc != returnTo) { std::fprintf(stderr, "case %u returned to 0x%x\n", index, ctx->pc); return 4; }
        for (int r = 0; r < 32; ++r) {
            words[2 * r] = (uint64_t)PS2_EXTRACT_EPI64_0(ctx->r[r]);
            words[2 * r + 1] = (uint64_t)PS2_EXTRACT_EPI64_1(ctx->r[r]);
        }
        words[62] = words[63] = 0;  // $ra holds the harness return address
        words[64] = ctx->hi; words[65] = ctx->lo; words[66] = ctx->hi1; words[67] = ctx->lo1;
        std::memcpy(mem, rdram.get() + @DATA_BASE@u, sizeof(mem));
        std::memcpy(fpu, ctx->f, 128); std::memcpy(fpu + 32, &ctx->f_acc, 4); fpu[33] = ctx->fcr31;
        std::memcpy(vu, ctx->vu0_vf, 512); std::memcpy(vu + 128, &ctx->vu0_acc, 16);
        std::memcpy(vu + 132, &ctx->vu0_q, 4); std::memcpy(vu + 133, &ctx->vu0_i, 4); vu[134] = ctx->vu0_clip_flags;
        vu[135] = ctx->vu0_mac_flags; vu[136] = ctx->vu0_status;
        for (int k = 0; k < 16; ++k) vu[137 + k] = ctx->vi[k];
        std::fwrite(words, 8, 68, stdout);
        std::fwrite(fpu, 4, 34, stdout);
        std::fwrite(vu, 4, 153, stdout);
        std::fwrite(mem, 1, sizeof(mem), stdout);
        std::fwrite(g_vu0Data, 1, sizeof(g_vu0Data), stdout);
    }
    return 0;
}
'''

CONFIG = '''[general]
input = {input}
output = {output}
ghidra_output = ""
single_file_output = false
output_worker_threads = 1
low_memory_mode = true
patch_syscalls = false
patch_cop0 = true
patch_cache = true
stubs = []
skip = []
'''


def translate(work, code, lab):
    """Run the pinned recompiler over the synthetic executable; returns the generated directory."""
    from overlays import wrap_elf
    elf = work / 'cases.elf'
    elf.write_bytes(wrap_elf(code, CODE_BASE))
    generated = work / 'generated'
    config = work / 'cases.toml'
    config.write_text(CONFIG.format(input=json.dumps(str(elf)), output=json.dumps(str(generated))))
    (work / 'logs').mkdir(exist_ok=True)
    lab.run([lab.BUILD / 'ps2xRecomp/ps2_recomp', config], work / 'logs/translate.log', offline=True, cwd=work)
    from knowledge import apply_translation
    apply_translation(generated, identity=None)  # the generic rules every title receives
    return generated


def compile_harness(work, generated, starts, lab):
    names = []
    header = (generated / 'ps2_recompiled_functions.h').read_text()
    for start in starts:
        token = f'_0x{start:x}('
        name = next((line.split()[1].split('(')[0] for line in header.splitlines() if token in line), None)
        if name is None:
            raise RuntimeError(f'No translated function at 0x{start:x}')
        names.append(name)
    source = (HARNESS.replace('@FUNCTIONS@', ', '.join(names))
              .replace('@STARTS@', ', '.join(f'0x{s:x}u' for s in starts))
              .replace('@DATA@', str(DATA_BYTES)).replace('@VU0@', str(VU0_DATA_BYTES)).replace('@DATA_BASE@', f'0x{DATA_BASE:x}'))
    (work / 'harness.cpp').write_text(source)
    command = [part for part in lab.syntax_check_command(generated) if part not in ('-fsyntax-only', '-ferror-limit=8')]
    command += ['-O1', '-w', str(work / 'harness.cpp')]
    command += [str(generated / f'{name}.cpp') for name in names]
    # Runtime members are only reached for special addresses and calls, which no case uses.
    command += ['-Wl,-undefined,dynamic_lookup'] if platform.system() == 'Darwin' else ['-Wl,--unresolved-symbols=ignore-all']
    command += ['-o', str(work / 'harness')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if built.returncode:
        raise RuntimeError('Harness did not compile:\n' + built.stderr[-4000:])
    return work / 'harness'


def describe(state, other):
    diffs = []
    for r in range(32):
        if r == RA:
            continue
        for half, label in ((0, ''), (1, '.hi64')):
            if state.gpr[r][half] != other.gpr[r][half]:
                diffs.append(f'r{r}{label} {other.gpr[r][half]:#x} expected {state.gpr[r][half]:#x}')
    for key in ('hi', 'lo', 'hi1', 'lo1'):
        if getattr(state, key) != getattr(other, key):
            diffs.append(f'{key} {getattr(other, key):#x} expected {getattr(state, key):#x}')
    for r in range(32):
        if state.fpr[r] != other.fpr[r]:
            diffs.append(f'f{r} {other.fpr[r]:#010x} expected {state.fpr[r]:#010x}')
    if state.acc != other.acc:
        diffs.append(f'acc {other.acc:#010x} expected {state.acc:#010x}')
    for r in range(32):
        for k in range(4):
            if state.vf[r][k] != other.vf[r][k]:
                diffs.append(f'vf{r}.{"xyzw"[k]} {other.vf[r][k]:#010x} expected {state.vf[r][k]:#010x}')
    for k in range(4):
        if state.vacc[k] != other.vacc[k]:
            diffs.append(f'vacc.{"xyzw"[k]} {other.vacc[k]:#010x} expected {state.vacc[k]:#010x}')
    if (state.mac ^ other.mac) & MAC_FLAGS:
        diffs.append(f'mac {other.mac & MAC_FLAGS:#06x} expected {state.mac & MAC_FLAGS:#06x}')
    if (state.status ^ other.status) & STATUS_FLAGS:
        diffs.append(f'status {other.status & STATUS_FLAGS:#05x} expected {state.status & STATUS_FLAGS:#05x}')
    for key in ('q', 'i', 'clip'):
        if getattr(state, key) != getattr(other, key):
            diffs.append(f'{key} {getattr(other, key):#010x} expected {getattr(state, key):#010x}')
    for r in range(16):
        if (state.vi[r] ^ other.vi[r]) & 0xFFFF:
            diffs.append(f'vi{r} {other.vi[r]:#x} expected {state.vi[r]:#x}')
    if (state.fcr31 ^ other.fcr31) & FCR31_FLAGS:
        diffs.append(f'fcr31 {other.fcr31 & FCR31_FLAGS:#08x} expected {state.fcr31 & FCR31_FLAGS:#08x}')
    if state.mem != other.mem:
        first = next(i for i in range(DATA_BYTES) if state.mem[i] != other.mem[i])
        diffs.append(f'memory from {DATA_BASE + first:#x}')
    if state.vumem != other.vumem:
        first = next(i for i in range(VU0_DATA_BYTES) if state.vumem[i] != other.vumem[i])
        diffs.append(f'VU0 data memory from {first:#x}')
    return diffs


def _float_slots(state):
    return state.fpr + [state.acc] + [w for r in state.vf for w in r] + state.vacc + [state.q, state.i]


def rounding_only(want, got, steps=1):
    """True when the only differences are float results at most `steps` bit-steps apart with the
    same sign: the host's round-to-nearest against the EE's round-toward-zero, a deviation the engine
    accepts (see rounding_steps)."""
    if describe(_strip_floats(want), _strip_floats(got)):
        return False
    pairs = [(a, b) for a, b in zip(_float_slots(want), _float_slots(got)) if a != b]
    return bool(pairs) and all((a ^ b) >> 31 == 0 and abs(a - b) <= steps for a, b in pairs)


def expected_host(name, fields, state):
    """The model with multiply-add products rounded to nearest (the engine's host rounding policy)."""
    _HOST_PRODUCT.append(True)
    try:
        return expected(name, fields, state)
    finally:
        _HOST_PRODUCT.clear()


def rounding_difference(name, fields, before, want, got):
    """True when got differs from the exact model only by the accepted host rounding."""
    steps = rounding_steps(name)
    if rounding_only(want, got, steps):
        return True
    if steps == 1:
        return False
    host = expected_host(name, fields, before)
    return not describe(host, got) or rounding_only(host, got, steps)


def rounding_steps(name):
    """Last-bit steps the host-rounding policy allows: one per rounding, and a reciprocal root also
    carries its rounded root's error into the quotient."""
    if 'rsqrt' in name:
        return 3
    return 2 if any(part in name for part in ('madd', 'msub', 'vopmsub')) else 1


def _strip_floats(state):
    out = state.copy()
    out.fpr, out.acc, out.vf, out.vacc, out.q, out.i = [0] * 32, 0, [[0] * 4 for _ in range(32)], [0] * 4, 0, 0
    return out


def check(states=64, seed=1, variants=3, work=None, only=None):
    import pipeline as lab
    rng = random.Random(seed)
    case_list = cases(rng, variants)
    if only:
        case_list = [c for c in case_list if c[0] in only]
    code, starts = program(case_list)
    for (name, _, fields), start in zip(case_list, starts):
        if name in CALL_LAYOUTS:
            fields['start'] = start
    owned = work is None
    work = Path(tempfile.mkdtemp(prefix='behaviour-check-')) if owned else Path(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        generated = translate(work, code, lab)
        harness = compile_harness(work, generated, starts, lab)
        inputs, expectations = bytearray(), []
        for index, (name, _, fields) in enumerate(case_list):
            made = tries = 0
            while made < states:
                tries += 1
                if tries > 100 * states:
                    raise RuntimeError(f'{name}: the input generator rarely produces inputs inside the check')
                state = random_state(rng, name, fields)
                try:
                    after = expected(name, fields, state)
                except Trap:
                    continue
                inputs += struct.pack('<I', index) + pack(state)
                expectations.append((index, state, after))
                made += 1
        ran = subprocess.run([str(harness)], input=bytes(inputs), capture_output=True, timeout=600)
        if ran.returncode:
            raise RuntimeError(f'Harness failed ({ran.returncode}): {ran.stderr.decode(errors="replace")[-2000:]}')
        size = struct.calcsize(STATE_FORMAT)
        if len(ran.stdout) != size * len(expectations):
            raise RuntimeError('Harness output is truncated')
        forms = {}
        for k, (index, before, after) in enumerate(expectations):
            name, words, fields = case_list[index]
            actual = unpack(ran.stdout[k * size:(k + 1) * size])
            record = forms.setdefault(name, dict(cases=0, failures=0, rounding=0, examples=[]))
            record['cases'] += 1
            diffs = describe(after, actual)
            if diffs and rounding_difference(name, fields, before, after, actual):
                record['rounding'] += 1
            elif diffs:
                record['failures'] += 1
                if len(record['examples']) < 3:
                    record['examples'].append(dict(word=f'{words[0]:#010x}', fields={k2: fields[k2] for k2 in fields},
                                                   inputs={f'r{r}': f'{before.gpr[r][0]:#x}' for r in sorted({fields.get("rs", 0), fields.get("rt", 0)}) if r},
                                                   hi=f'{before.hi:#x}', lo=f'{before.lo:#x}', differences=diffs))
        failing = sorted(n for n, r in forms.items() if r['failures'])
        rounding = sorted(n for n, r in forms.items() if r['rounding'])
        return dict(schema=1, seed=seed, states_per_case=states, forms=len(forms), cases=len(case_list),
                    executions=len(expectations), failing_forms=failing, rounding_forms=rounding, passed=not failing, results=forms)
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--states', type=int, default=64)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--work')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    report = check(args.states, args.seed, work=args.work, only=args.only)
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + '\n')
    print(f"{report['forms']} forms, {report['executions']} executions: "
          + ('all match the reference model' if report['passed'] else 'mismatches in ' + ', '.join(report['failing_forms']))
          + (f"; last-bit rounding only in {len(report['rounding_forms'])} forms" if report['rounding_forms'] else ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
