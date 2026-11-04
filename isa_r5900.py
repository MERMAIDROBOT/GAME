"""R5900 (PS2 EE) instruction table: exact decode and encode.

Each spec names fixed bit fields and operand fields that together cover all 32 bits, so a decoded word always
re-encodes to the same word. Words no spec covers (unusual reserved bits, unlisted opcodes) are not instructions to
this table; callers keep them as data words. No game data lives here.
"""
import re

FIELDS = {'op': (26, 6), 'rs': (21, 5), 'rt': (16, 5), 'rd': (11, 5), 'sa': (6, 5), 'funct': (0, 6),
          'imm': (0, 16), 'target': (0, 26), 'code': (6, 20), 'code10': (6, 10), 'co': (25, 1), 'dest': (21, 4),
          'ftf': (23, 2), 'fsf': (21, 2), 'imm15': (6, 15), 'il': (0, 1), 'low10': (1, 10), 'low11': (0, 11),
          'low25': (0, 25), 'hi2': (24, 2)}

GPR = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
       's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']
GPR_INDEX = {name: i for i, name in enumerate(GPR)}
GPR_INDEX['s8'] = 30

# Operand kinds: name -> (field, printer kind)
OPERANDS = {
    'rs': ('rs', 'gpr'), 'rt': ('rt', 'gpr'), 'rd': ('rd', 'gpr'), 'sa': ('sa', 'u'),
    'simm': ('imm', 's16'), 'uimm': ('imm', 'u16'), 'mem': (('imm', 'rs'), 'mem'), 'br': ('imm', 'branch'),
    'jt': ('target', 'jump'), 'code': ('code', 'u'), 'code10': ('code10', 'u'), 'hint': ('rt', 'u'),
    'fd': ('sa', 'fpr'), 'fs': ('rd', 'fpr'), 'ft': ('rt', 'fpr'), 'c0r': ('rd', 'u'), 'c1c': ('rd', 'u'),
    'vfd': ('sa', 'vf'), 'vfs': ('rd', 'vf'), 'vft': ('rt', 'vf'), 'vid': ('sa', 'vi'), 'vis': ('rd', 'vi'),
    'vit': ('rt', 'vi'), 'c2vf': ('rd', 'vf'), 'c2vi': ('rd', 'vi'), 'imm5': ('sa', 's5'), 'imm15': ('imm15', 'u'),
    'fsf': ('fsf', 'u'), 'ftf': ('ftf', 'u'),
}

SPECS = []          # (mnemonic, fixed {field: value}, operands, suffix fields)


def _spec(mnemonic, fixed, operands=(), suffix=()):
    SPECS.append((mnemonic, dict(fixed), tuple(operands), tuple(suffix)))


def _bits(field):
    shift, width = FIELDS[field]
    return ((1 << width) - 1) << shift


def _operand_fields(operand):
    field = OPERANDS[operand][0]
    return field if isinstance(field, tuple) else (field,)


# ---- integer core -------------------------------------------------------------------------------------------
R3 = ('rd', 'rs', 'rt')
for name, funct in [('add', 0x20), ('addu', 0x21), ('sub', 0x22), ('subu', 0x23), ('and', 0x24), ('or', 0x25),
                    ('xor', 0x26), ('nor', 0x27), ('slt', 0x2a), ('sltu', 0x2b), ('dadd', 0x2c), ('daddu', 0x2d),
                    ('dsub', 0x2e), ('dsubu', 0x2f), ('movz', 0x0a), ('movn', 0x0b), ('mult', 0x18), ('multu', 0x19)]:
    _spec(name, {'op': 0, 'funct': funct, 'sa': 0}, R3)
for name, funct in [('sll', 0), ('srl', 2), ('sra', 3), ('dsll', 0x38), ('dsrl', 0x3a), ('dsra', 0x3b),
                    ('dsll32', 0x3c), ('dsrl32', 0x3e), ('dsra32', 0x3f)]:
    _spec(name, {'op': 0, 'funct': funct, 'rs': 0}, ('rd', 'rt', 'sa'))
for name, funct in [('sllv', 4), ('srlv', 6), ('srav', 7), ('dsllv', 0x14), ('dsrlv', 0x16), ('dsrav', 0x17)]:
    _spec(name, {'op': 0, 'funct': funct, 'sa': 0}, ('rd', 'rt', 'rs'))
_spec('jr', {'op': 0, 'funct': 8, 'rt': 0, 'rd': 0, 'sa': 0}, ('rs',))
_spec('jalr', {'op': 0, 'funct': 9, 'rt': 0, 'sa': 0}, ('rd', 'rs'))
_spec('syscall', {'op': 0, 'funct': 0x0c}, ('code',))
_spec('break', {'op': 0, 'funct': 0x0d}, ('code',))
_spec('sync', {'op': 0, 'funct': 0x0f, 'rs': 0, 'rt': 0, 'rd': 0}, ('sa',))
for name, funct in [('mfhi', 0x10), ('mflo', 0x12), ('mfsa', 0x28)]:
    _spec(name, {'op': 0, 'funct': funct, 'rs': 0, 'rt': 0, 'sa': 0}, ('rd',))
for name, funct in [('mthi', 0x11), ('mtlo', 0x13), ('mtsa', 0x29)]:
    _spec(name, {'op': 0, 'funct': funct, 'rt': 0, 'rd': 0, 'sa': 0}, ('rs',))
for name, funct in [('div', 0x1a), ('divu', 0x1b)]:
    _spec(name, {'op': 0, 'funct': funct, 'rd': 0, 'sa': 0}, ('rs', 'rt'))
for name, funct in [('tge', 0x30), ('tgeu', 0x31), ('tlt', 0x32), ('tltu', 0x33), ('teq', 0x34), ('tne', 0x36)]:
    _spec(name, {'op': 0, 'funct': funct}, ('rs', 'rt', 'code10'))

for name, rt in [('bltz', 0), ('bgez', 1), ('bltzl', 2), ('bgezl', 3), ('bltzal', 0x10), ('bgezal', 0x11),
                 ('bltzall', 0x12), ('bgezall', 0x13)]:
    _spec(name, {'op': 1, 'rt': rt}, ('rs', 'br'))
for name, rt in [('tgei', 8), ('tgeiu', 9), ('tlti', 0xa), ('tltiu', 0xb), ('teqi', 0xc), ('tnei', 0xe),
                 ('mtsab', 0x18), ('mtsah', 0x19)]:
    _spec(name, {'op': 1, 'rt': rt}, ('rs', 'simm'))

_spec('j', {'op': 2}, ('jt',))
_spec('jal', {'op': 3}, ('jt',))
for name, op in [('beq', 4), ('bne', 5), ('beql', 0x14), ('bnel', 0x15)]:
    _spec(name, {'op': op}, ('rs', 'rt', 'br'))
for name, op in [('blez', 6), ('bgtz', 7), ('blezl', 0x16), ('bgtzl', 0x17)]:
    _spec(name, {'op': op, 'rt': 0}, ('rs', 'br'))
for name, op in [('addi', 8), ('addiu', 9), ('slti', 0xa), ('sltiu', 0xb), ('daddi', 0x18), ('daddiu', 0x19)]:
    _spec(name, {'op': op}, ('rt', 'rs', 'simm'))
for name, op in [('andi', 0xc), ('ori', 0xd), ('xori', 0xe)]:
    _spec(name, {'op': op}, ('rt', 'rs', 'uimm'))
_spec('lui', {'op': 0xf, 'rs': 0}, ('rt', 'uimm'))
for name, op in [('ldl', 0x1a), ('ldr', 0x1b), ('lq', 0x1e), ('sq', 0x1f), ('lb', 0x20), ('lh', 0x21),
                 ('lwl', 0x22), ('lw', 0x23), ('lbu', 0x24), ('lhu', 0x25), ('lwr', 0x26), ('lwu', 0x27),
                 ('sb', 0x28), ('sh', 0x29), ('swl', 0x2a), ('sw', 0x2b), ('sdl', 0x2c), ('sdr', 0x2d),
                 ('swr', 0x2e), ('ld', 0x37), ('sd', 0x3f)]:
    _spec(name, {'op': op}, ('rt', 'mem'))
_spec('cache', {'op': 0x2f}, ('hint', 'mem'))
_spec('pref', {'op': 0x33}, ('hint', 'mem'))
_spec('lwc1', {'op': 0x31}, ('ft', 'mem'))
_spec('swc1', {'op': 0x39}, ('ft', 'mem'))
_spec('lqc2', {'op': 0x36}, ('vft', 'mem'))
_spec('sqc2', {'op': 0x3e}, ('vft', 'mem'))

# ---- COP0 ---------------------------------------------------------------------------------------------------
_spec('mfc0', {'op': 0x10, 'rs': 0, 'low11': 0}, ('rt', 'c0r'))
_spec('mtc0', {'op': 0x10, 'rs': 4, 'low11': 0}, ('rt', 'c0r'))
for name, rt in [('bc0f', 0), ('bc0t', 1), ('bc0fl', 2), ('bc0tl', 3)]:
    _spec(name, {'op': 0x10, 'rs': 8, 'rt': rt}, ('br',))
for name, funct in [('tlbr', 1), ('tlbwi', 2), ('tlbwr', 6), ('tlbp', 8), ('eret', 0x18), ('ei', 0x38), ('di', 0x39)]:
    _spec(name, {'op': 0x10, 'rs': 0x10, 'rt': 0, 'rd': 0, 'sa': 0, 'funct': funct})

# ---- COP1 (single precision FPU) ----------------------------------------------------------------------------
_spec('mfc1', {'op': 0x11, 'rs': 0, 'low11': 0}, ('rt', 'fs'))
_spec('mtc1', {'op': 0x11, 'rs': 4, 'low11': 0}, ('rt', 'fs'))
_spec('cfc1', {'op': 0x11, 'rs': 2, 'low11': 0}, ('rt', 'c1c'))
_spec('ctc1', {'op': 0x11, 'rs': 6, 'low11': 0}, ('rt', 'c1c'))
for name, rt in [('bc1f', 0), ('bc1t', 1), ('bc1fl', 2), ('bc1tl', 3)]:
    _spec(name, {'op': 0x11, 'rs': 8, 'rt': rt}, ('br',))
S = {'op': 0x11, 'rs': 0x10}
for name, funct in [('add.s', 0), ('sub.s', 1), ('mul.s', 2), ('div.s', 3), ('rsqrt.s', 0x16), ('madd.s', 0x1c),
                    ('msub.s', 0x1d), ('max.s', 0x28), ('min.s', 0x29)]:
    _spec(name, dict(S, funct=funct), ('fd', 'fs', 'ft'))
_spec('sqrt.s', dict(S, funct=4, rd=0), ('fd', 'ft'))
for name, funct in [('abs.s', 5), ('mov.s', 6), ('neg.s', 7), ('cvt.w.s', 0x24)]:
    _spec(name, dict(S, funct=funct, rt=0), ('fd', 'fs'))
for name, funct in [('adda.s', 0x18), ('suba.s', 0x19), ('mula.s', 0x1a), ('madda.s', 0x1e), ('msuba.s', 0x1f),
                    ('c.f.s', 0x30), ('c.eq.s', 0x32), ('c.lt.s', 0x34), ('c.le.s', 0x36)]:
    _spec(name, dict(S, funct=funct, sa=0), ('fs', 'ft'))
_spec('cvt.s.w', {'op': 0x11, 'rs': 0x14, 'funct': 0x20, 'rt': 0}, ('fd', 'fs'))

# ---- COP2 (VU0 macro mode) ----------------------------------------------------------------------------------
for name, rs in [('qmfc2', 1), ('qmtc2', 5)]:
    _spec(name, {'op': 0x12, 'rs': rs, 'low10': 0}, ('rt', 'c2vf'), ('il',))
for name, rs in [('cfc2', 2), ('ctc2', 6)]:
    _spec(name, {'op': 0x12, 'rs': rs, 'low10': 0}, ('rt', 'c2vi'), ('il',))
for name, rt in [('bc2f', 0), ('bc2t', 1), ('bc2fl', 2), ('bc2tl', 3)]:
    _spec(name, {'op': 0x12, 'rs': 8, 'rt': rt}, ('br',))
CO = {'op': 0x12, 'co': 1}
BC = 'xyzw'
for base, start in [('vadd', 0x00), ('vsub', 0x04), ('vmadd', 0x08), ('vmsub', 0x0c), ('vmax', 0x10),
                    ('vmini', 0x14), ('vmul', 0x18)]:
    for i, lane in enumerate(BC):
        _spec(base + lane, dict(CO, funct=start + i), ('vfd', 'vfs', 'vft'), ('dest',))
for name, funct in [('vmulq', 0x1c), ('vmaxi', 0x1d), ('vmuli', 0x1e), ('vminii', 0x1f), ('vaddq', 0x20),
                    ('vmaddq', 0x21), ('vaddi', 0x22), ('vmaddi', 0x23), ('vsubq', 0x24), ('vmsubq', 0x25),
                    ('vsubi', 0x26), ('vmsubi', 0x27)]:
    _spec(name, dict(CO, funct=funct, rt=0), ('vfd', 'vfs'), ('dest',))
for name, funct in [('vadd', 0x28), ('vmadd', 0x29), ('vmul', 0x2a), ('vmax', 0x2b), ('vsub', 0x2c),
                    ('vmsub', 0x2d), ('vopmsub', 0x2e), ('vmini', 0x2f)]:
    _spec(name, dict(CO, funct=funct), ('vfd', 'vfs', 'vft'), ('dest',))
for name, funct in [('viadd', 0x30), ('visub', 0x31), ('viand', 0x34), ('vior', 0x35)]:
    _spec(name, dict(CO, funct=funct, dest=0), ('vid', 'vis', 'vit'))
_spec('viaddi', dict(CO, funct=0x32, dest=0), ('vit', 'vis', 'imm5'))
_spec('vcallms', dict(CO, funct=0x38, dest=0), ('imm15',))
_spec('vcallmsr', dict(CO, funct=0x39, dest=0, rt=0, rd=0, sa=0))


def _s2(name, code, operands, suffix=(), zero=()):
    fixed = dict(CO, sa=code >> 2, funct=0x3c | (code & 3))
    for field in zero:
        fixed[field] = 0
    _spec(name, fixed, operands, suffix)


for base, start in [('vadda', 0x00), ('vsuba', 0x04), ('vmadda', 0x08), ('vmsuba', 0x0c), ('vmula', 0x18)]:
    for i, lane in enumerate(BC):
        _s2(base + lane, start + i, ('vfs', 'vft'), ('dest',))
for i, name in enumerate(['vitof0', 'vitof4', 'vitof12', 'vitof15', 'vftoi0', 'vftoi4', 'vftoi12', 'vftoi15']):
    _s2(name, 0x10 + i, ('vft', 'vfs'), ('dest',))
for name, code in [('vmulaq', 0x1c), ('vmulai', 0x1e), ('vaddaq', 0x20), ('vmaddaq', 0x21), ('vaddai', 0x22),
                   ('vmaddai', 0x23), ('vsubaq', 0x24), ('vmsubaq', 0x25), ('vsubai', 0x26), ('vmsubai', 0x27)]:
    _s2(name, code, ('vfs',), ('dest',), zero=('rt',))
_s2('vabs', 0x1d, ('vft', 'vfs'), ('dest',))
_s2('vclipw', 0x1f, ('vfs', 'vft'), ('dest',))
for name, code in [('vadda', 0x28), ('vmadda', 0x29), ('vmula', 0x2a), ('vsuba', 0x2c), ('vmsuba', 0x2d),
                   ('vopmula', 0x2e)]:
    _s2(name, code, ('vfs', 'vft'), ('dest',))
_s2('vnop', 0x2f, (), zero=('dest', 'rt', 'rd'))
_s2('vmove', 0x30, ('vft', 'vfs'), ('dest',))
_s2('vmr32', 0x31, ('vft', 'vfs'), ('dest',))
_s2('vlqi', 0x34, ('vft', 'vis'), ('dest',))
_s2('vsqi', 0x35, ('vfs', 'vit'), ('dest',))
_s2('vlqd', 0x36, ('vft', 'vis'), ('dest',))
_s2('vsqd', 0x37, ('vfs', 'vit'), ('dest',))
for name, code in [('vdiv', 0x38), ('vsqrt', 0x39), ('vrsqrt', 0x3a)]:
    _s2(name, code, ('vfs', 'fsf', 'vft', 'ftf'))
_s2('vwaitq', 0x3b, (), zero=('dest', 'rt', 'rd'))
_s2('vmtir', 0x3c, ('vit', 'vfs', 'fsf'), zero=('ftf',))
_s2('vmfir', 0x3d, ('vft', 'vis'), ('dest',))
_s2('vilwr', 0x3e, ('vit', 'vis'), ('dest',))
_s2('viswr', 0x3f, ('vit', 'vis'), ('dest',))
_s2('vrnext', 0x40, ('vft',), ('dest',), zero=('rd',))
_s2('vrget', 0x41, ('vft',), ('dest',), zero=('rd',))
_s2('vrinit', 0x42, ('vfs', 'fsf'), zero=('ftf', 'rt'))
_s2('vrxor', 0x43, ('vfs', 'fsf'), zero=('ftf', 'rt'))

# ---- MMI ----------------------------------------------------------------------------------------------------
M = {'op': 0x1c}
for name, funct in [('madd', 0), ('maddu', 1), ('mult1', 0x18), ('multu1', 0x19), ('madd1', 0x20), ('maddu1', 0x21)]:
    _spec(name, dict(M, funct=funct, sa=0), R3)
_spec('plzcw', dict(M, funct=4, rt=0, sa=0), ('rd', 'rs'))
for name, funct in [('mfhi1', 0x10), ('mflo1', 0x12)]:
    _spec(name, dict(M, funct=funct, rs=0, rt=0, sa=0), ('rd',))
for name, funct in [('mthi1', 0x11), ('mtlo1', 0x13)]:
    _spec(name, dict(M, funct=funct, rt=0, rd=0, sa=0), ('rs',))
for name, funct in [('div1', 0x1a), ('divu1', 0x1b)]:
    _spec(name, dict(M, funct=funct, rd=0, sa=0), ('rs', 'rt'))
for i, fmt in enumerate(['lw', 'uw', 'slw', 'lh', 'sh']):
    _spec('pmfhl.' + fmt, dict(M, funct=0x30, sa=i, rs=0, rt=0), ('rd',))
_spec('pmthl.lw', dict(M, funct=0x31, sa=0, rt=0, rd=0), ('rs',))
for name, funct in [('psllh', 0x34), ('psrlh', 0x36), ('psrah', 0x37), ('psllw', 0x3c), ('psrlw', 0x3e),
                    ('psraw', 0x3f)]:
    _spec(name, dict(M, funct=funct, rs=0), ('rd', 'rt', 'sa'))
MMI = {
    0x08: {0x00: 'paddw', 0x01: 'psubw', 0x02: 'pcgtw', 0x03: 'pmaxw', 0x04: 'paddh', 0x05: 'psubh', 0x06: 'pcgth',
           0x07: 'pmaxh', 0x08: 'paddb', 0x09: 'psubb', 0x0a: 'pcgtb', 0x10: 'paddsw', 0x11: 'psubsw',
           0x12: 'pextlw', 0x13: 'ppacw', 0x14: 'paddsh', 0x15: 'psubsh', 0x16: 'pextlh', 0x17: 'ppach',
           0x18: 'paddsb', 0x19: 'psubsb', 0x1a: 'pextlb', 0x1b: 'ppacb', 0x1e: 'pext5', 0x1f: 'ppac5'},
    0x28: {0x01: 'pabsw', 0x02: 'pceqw', 0x03: 'pminw', 0x04: 'padsbh', 0x05: 'pabsh', 0x06: 'pceqh', 0x07: 'pminh',
           0x0a: 'pceqb', 0x10: 'padduw', 0x11: 'psubuw', 0x12: 'pextuw', 0x14: 'padduh', 0x15: 'psubuh',
           0x16: 'pextuh', 0x18: 'paddub', 0x19: 'psubub', 0x1a: 'pextub', 0x1b: 'qfsrv'},
    0x09: {0x00: 'pmaddw', 0x02: 'psllvw', 0x03: 'psrlvw', 0x04: 'pmsubw', 0x08: 'pmfhi', 0x09: 'pmflo',
           0x0a: 'pinth', 0x0c: 'pmultw', 0x0d: 'pdivw', 0x0e: 'pcpyld', 0x10: 'pmaddh', 0x11: 'phmadh',
           0x12: 'pand', 0x13: 'pxor', 0x14: 'pmsubh', 0x15: 'phmsbh', 0x1a: 'pexeh', 0x1b: 'prevh',
           0x1c: 'pmulth', 0x1d: 'pdivbw', 0x1e: 'pexew', 0x1f: 'prot3w'},
    0x29: {0x00: 'pmadduw', 0x03: 'psravw', 0x08: 'pmthi', 0x09: 'pmtlo', 0x0a: 'pinteh', 0x0c: 'pmultuw',
           0x0d: 'pdivuw', 0x0e: 'pcpyud', 0x12: 'por', 0x13: 'pnor', 0x1a: 'pexch', 0x1b: 'pcpyh', 0x1e: 'pexcw'},
}
RD_RT = {'pabsw', 'pabsh', 'pext5', 'ppac5', 'pexeh', 'prevh', 'pexew', 'prot3w', 'pexch', 'pcpyh', 'pexcw'}
for funct, table in MMI.items():
    for sa, name in table.items():
        fixed = dict(M, funct=funct, sa=sa)
        if name in ('pmfhi', 'pmflo'):
            _spec(name, dict(fixed, rs=0, rt=0), ('rd',))
        elif name in ('pmthi', 'pmtlo'):
            _spec(name, dict(fixed, rt=0, rd=0), ('rs',))
        elif name in ('pdivw', 'pdivuw', 'pdivbw'):
            _spec(name, dict(fixed, rd=0), ('rs', 'rt'))
        elif name in RD_RT:
            _spec(name, dict(fixed, rs=0), ('rd', 'rt'))
        else:
            _spec(name, fixed, R3)


# ---- tables -------------------------------------------------------------------------------------------------
class Spec:
    __slots__ = ('mnemonic', 'fixed', 'operands', 'suffix', 'mask', 'match')

    def __init__(self, mnemonic, fixed, operands, suffix):
        self.mnemonic, self.fixed, self.operands, self.suffix = mnemonic, fixed, operands, suffix
        covered, mask, match = 0, 0, 0
        for field, value in fixed.items():
            bits = _bits(field)
            if covered & bits:
                raise ValueError(f'{mnemonic}: fixed field {field} overlaps')
            shift, width = FIELDS[field]
            if value >> width:
                raise ValueError(f'{mnemonic}: {field} value too wide')
            covered |= bits; mask |= bits; match |= value << shift
        for operand in operands + suffix:
            for field in (_operand_fields(operand) if operand in OPERANDS else (operand,)):
                bits = _bits(field)
                if covered & bits:
                    raise ValueError(f'{mnemonic}: operand field {field} overlaps')
                covered |= bits
        if covered != 0xffffffff:
            raise ValueError(f'{mnemonic}: fields cover {covered:#010x}, not all 32 bits')
        self.mask, self.match = mask, match


TABLE = [Spec(*s) for s in SPECS]
BY_OP = {}
for spec in TABLE:
    BY_OP.setdefault(spec.match >> 26, []).append(spec)
BY_NAME = {}
for spec in TABLE:
    BY_NAME.setdefault(spec.mnemonic, []).append(spec)


def overlaps():
    """Pairs of specs that can match the same word (the table must have none)."""
    found = []
    for specs in BY_OP.values():
        for i, a in enumerate(specs):
            for b in specs[i + 1:]:
                if not ((a.mask & b.mask) & (a.match ^ b.match)):
                    found.append((a.mnemonic, b.mnemonic))
    return found


def field(word, name):
    shift, width = FIELDS[name]
    return (word >> shift) & ((1 << width) - 1)


def decode(word):
    """(spec, {operand: raw value}) or None."""
    for spec in BY_OP.get(word >> 26, ()):
        if word & spec.mask == spec.match:
            values = {}
            for operand in spec.operands:
                fields = _operand_fields(operand)
                values[operand] = tuple(field(word, f) for f in fields) if len(fields) > 1 else field(word, fields[0])
            for name in spec.suffix:
                values[name] = field(word, name)
            return spec, values
    return None


def encode(spec, values):
    word = spec.match
    for operand in spec.operands:
        fields = _operand_fields(operand)
        raw = values[operand] if len(fields) > 1 else (values[operand],)
        for f, v in zip(fields, raw):
            shift, width = FIELDS[f]
            if v < 0 or v >> width:
                raise ValueError(f'{spec.mnemonic}: {operand} out of range')
            word |= v << shift
    for name in spec.suffix:
        shift, width = FIELDS[name]
        v = values[name]
        if v < 0 or v >> width:
            raise ValueError(f'{spec.mnemonic}: {name} out of range')
        word |= v << shift
    return word


# ---- text form ----------------------------------------------------------------------------------------------
def dest_text(value):
    return ''.join(lane for bit, lane in zip((8, 4, 2, 1), 'xyzw') if value & bit) or '-'


def parse_dest(text):
    if text == '-':
        return 0
    if not re.fullmatch('x?y?z?w?', text):
        raise ValueError('bad dest ' + text)
    return sum(bit for bit, lane in zip((8, 4, 2, 1), 'xyzw') if lane in text)


def sign(value, width):
    return value - (1 << width) if value >> (width - 1) else value


def mnemonic_text(spec, values):
    text = spec.mnemonic
    if 'dest' in spec.suffix:
        text += '.' + dest_text(values['dest'])
    if 'il' in spec.suffix:
        text += '.i' if values['il'] else '.ni'
    return text


def split_mnemonic(text):
    """Text mnemonic -> (spec candidates, suffix values)."""
    if text in BY_NAME:
        return BY_NAME[text], {}
    base, _, tail = text.rpartition('.')
    if base in BY_NAME:
        specs = BY_NAME[base]
        if tail in ('i', 'ni') and any('il' in s.suffix for s in specs):
            return [s for s in specs if 'il' in s.suffix], {'il': int(tail == 'i')}
        if any('dest' in s.suffix for s in specs):
            return [s for s in specs if 'dest' in s.suffix], {'dest': parse_dest(tail)}
    raise ValueError('unknown mnemonic ' + text)
