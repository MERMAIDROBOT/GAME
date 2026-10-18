# SPDX-License-Identifier: GPL-3.0-only
"""VU1 usage and live-input analysis, adapted from Devil Kings vu1rec.py.

Source: DavvidMac/PS2Recomp-DevilKings
Commit: b65592e383703e6def6e840f289681ba512a8903
Path: ps2xRuntime/tools/vu1rec.py (usage decoder and analysis portion only).
License: GPL-3.0; see tools/PS2Recomp-LICENSE.
Port Lab changes: remove emitter/filesystem entry points; validate image size;
wrap delay-slot PCs within micro memory; expose visited-state counts for an
aggregate budget. Serial diagnostic metadata only, not parallel eligibility.
"""
FMAC_LATENCY = 4
ACC_FORWARD_LATENCY = 1


class Reject(Exception):
    pass


def lane(component):
    return 1 << (3 - component)


def DEST(i):
    return (i >> 21) & 0xF


def FT(i):
    return (i >> 16) & 0x1F


def FS(i):
    return (i >> 11) & 0x1F


def FD(i):
    return (i >> 6) & 0x1F


def VIT(i):
    return (i >> 16) & 0xF


def VIS(i):
    return (i >> 11) & 0xF


def VID(i):
    return (i >> 6) & 0xF


def IMM11(i):
    value = i & 0x7FF
    return value - 0x800 if value & 0x400 else value


class Usage:
    def __init__(self):
        self.vf_read = []          # [(reg, lanes)]
        self.vf_write = (0, 0)     # (reg, lanes)
        self.vi_read = 0
        self.vi_write = 0
        self.acc_read = 0
        self.acc_write = 0
        self.latency = 0
        self.vf_latency = 0
        self.vi_latency = 0
        self.pipeline = 'none'
        self.wait_q = False
        self.wait_p = False
        self.reads_clip = False
        self.writes_clip = False
        self.delays_branch = False
        self.reserved = False

    def add_vf_read(self, reg, lanes):
        if lanes == 0:
            return
        for index, (existing, existing_lanes) in enumerate(self.vf_read):
            if existing == reg:
                self.vf_read[index] = (reg, existing_lanes | lanes)
                return
        if len(self.vf_read) < 2:
            self.vf_read.append((reg, lanes))

    def add_vf_write(self, reg, lanes):
        if reg == 0 or lanes == 0:
            return
        current, current_lanes = self.vf_write
        if current == 0:
            self.vf_write = (reg, lanes)
        elif current == reg:
            self.vf_write = (reg, current_lanes | lanes)

    def read_vi(self, reg):
        if reg:
            self.vi_read |= 1 << reg

    def write_vi(self, reg):
        if reg:
            self.vi_write |= 1 << reg

    def vf_read_lanes(self, reg):
        for existing, lanes in self.vf_read:
            if existing == reg:
                return lanes
        return 0


PRODUCT_SUM_OPS = {0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x21, 0x23, 0x25, 0x27, 0x29, 0x2D, 0x2E}
PRODUCT_SUM_SPECIAL = {0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x21, 0x23, 0x25, 0x27, 0x29, 0x2D}


def upper_special(upper):
    return (upper & 3) | ((upper >> 4) & 0x7C)


def decode_upper(upper):
    usage = Usage()
    usage.pipeline = 'fmac'
    usage.latency = FMAC_LATENCY
    op = upper & 0x3F
    dest, fs, ft, fd = DEST(upper), FS(upper), FT(upper), FD(upper)
    if op <= 0x2F:
        usage.add_vf_read(fs, dest)
        usage.add_vf_write(fd, dest)
        if op <= 0x1B:
            usage.add_vf_read(ft, lane(op & 3))
        elif op >= 0x28:
            usage.add_vf_read(ft, 0xE if op == 0x2E else dest)
        if op in PRODUCT_SUM_OPS:
            usage.acc_read = dest
        return usage
    if op >= 0x3C:
        special = upper_special(upper)
        writes_acc = (special <= 0x0F or 0x18 <= special <= 0x1C or special == 0x1E or
                      0x20 <= special <= 0x2A or 0x2C <= special <= 0x2E)
        if writes_acc:
            usage.add_vf_read(fs, dest)
            if special <= 0x1B:
                usage.add_vf_read(ft, lane(special & 3))
            elif 0x28 <= special <= 0x2E:
                usage.add_vf_read(ft, 0xE if special == 0x2E else dest)
            usage.acc_write = dest
            if special in PRODUCT_SUM_SPECIAL:
                usage.acc_read = dest
        elif 0x10 <= special <= 0x17 or special == 0x1D:
            usage.add_vf_read(fs, dest)
            usage.add_vf_write(ft, dest)
        elif special == 0x1F:
            usage.add_vf_read(fs, 0xE)
            usage.add_vf_read(ft, 0x1)
            usage.writes_clip = True
        elif special not in (0x2F, 0x30):
            usage.reserved = True
        return usage
    usage.reserved = True
    return usage


def lower_special(lower):
    return (lower & 3) | ((lower >> 4) & 0x7C)


def decode_lower(lower):
    usage = Usage()
    if lower in (0, 0x8000033C):
        return usage
    op_hi = (lower >> 25) & 0x7F
    vf_t, vf_s = FT(lower), FS(lower)
    vi_t, vi_s, vi_d = VIT(lower), VIS(lower), VID(lower)
    dest = DEST(lower)
    if op_hi == 0x00:
        usage.pipeline, usage.latency = 'lsu', 4
        usage.read_vi(vi_s)
        usage.add_vf_write(vf_t, dest)
    elif op_hi == 0x01:
        usage.pipeline, usage.latency = 'lsu', 1
        usage.read_vi(vi_t)
        usage.add_vf_read(vf_s, dest)
    elif op_hi == 0x04:
        usage.pipeline, usage.latency = 'lsu', 4
        usage.read_vi(vi_s)
        usage.write_vi(vi_t)
    elif op_hi == 0x05:
        usage.pipeline, usage.latency = 'lsu', 1
        usage.read_vi(vi_s)
        usage.read_vi(vi_t)
    elif op_hi in (0x08, 0x09):
        usage.pipeline, usage.latency = 'ialu', 1
        usage.delays_branch = True
        usage.read_vi(vi_s)
        usage.write_vi(vi_t)
    elif op_hi in (0x10, 0x12, 0x13):
        usage.pipeline, usage.latency = 'ialu', 1
        usage.reads_clip = True
        usage.write_vi(1)
    elif op_hi == 0x11:
        usage.pipeline, usage.latency = 'fmac', FMAC_LATENCY
        usage.writes_clip = True
    elif op_hi in (0x14, 0x16, 0x17):
        usage.pipeline, usage.latency = 'ialu', 1
        usage.write_vi(vi_t)
    elif op_hi == 0x15:
        usage.pipeline, usage.latency = 'fmac', FMAC_LATENCY
    elif op_hi in (0x18, 0x1A, 0x1B):
        usage.pipeline, usage.latency = 'ialu', 1
        usage.read_vi(vi_s)
        usage.write_vi(vi_t)
    elif op_hi == 0x1C:
        usage.pipeline, usage.latency = 'ialu', 1
        usage.reads_clip = True
        usage.write_vi(vi_t)
    elif op_hi == 0x20:
        usage.pipeline = 'branch'
    elif op_hi == 0x21:
        usage.pipeline, usage.latency = 'branch', 1
        usage.write_vi(vi_t)
    elif op_hi == 0x24:
        usage.pipeline = 'branch'
        usage.read_vi(vi_s)
    elif op_hi == 0x25:
        usage.pipeline, usage.latency = 'branch', 1
        usage.read_vi(vi_s)
        usage.write_vi(vi_t)
    elif op_hi in (0x28, 0x29):
        usage.pipeline = 'branch'
        usage.read_vi(vi_s)
        usage.read_vi(vi_t)
    elif op_hi in (0x2C, 0x2D, 0x2E, 0x2F):
        usage.pipeline = 'branch'
        usage.read_vi(vi_s)
    elif op_hi == 0x40:
        direct = lower & 0x3F
        if direct in (0x30, 0x31, 0x34, 0x35):
            usage.pipeline, usage.latency = 'ialu', 1
            usage.delays_branch = True
            usage.read_vi(vi_s)
            usage.read_vi(vi_t)
            usage.write_vi(vi_d)
        elif direct == 0x32:
            usage.pipeline, usage.latency = 'ialu', 1
            usage.delays_branch = True
            usage.read_vi(vi_s)
            usage.write_vi(vi_t)
        elif direct < 0x3C:
            usage.reserved = True
        else:
            special = lower_special(lower)
            if special in (0x30, 0x31):
                usage.pipeline, usage.latency = 'fmac', 4
                usage.add_vf_read(vf_s, 0xF if special == 0x31 else dest)
                usage.add_vf_write(vf_t, dest)
            elif special in (0x34, 0x36):
                usage.pipeline, usage.latency, usage.vi_latency = 'lsu', 4, 1
                usage.delays_branch = True
                usage.read_vi(vi_s)
                usage.write_vi(vi_s)
                usage.add_vf_write(vf_t, dest)
            elif special in (0x35, 0x37):
                usage.pipeline, usage.latency = 'lsu', 1
                usage.delays_branch = True
                usage.read_vi(vi_t)
                usage.write_vi(vi_t)
                usage.add_vf_read(vf_s, dest)
            elif special == 0x38:
                usage.pipeline, usage.latency = 'fdiv', 7
                usage.add_vf_read(vf_s, lane((lower >> 21) & 3))
                usage.add_vf_read(vf_t, lane((lower >> 23) & 3))
            elif special == 0x39:
                usage.pipeline, usage.latency = 'fdiv', 7
                usage.add_vf_read(vf_t, lane((lower >> 23) & 3))
            elif special == 0x3A:
                usage.pipeline, usage.latency = 'fdiv', 13
                usage.add_vf_read(vf_s, lane((lower >> 21) & 3))
                usage.add_vf_read(vf_t, lane((lower >> 23) & 3))
            elif special == 0x3B:
                usage.pipeline = 'fdiv'
                usage.wait_q = True
            elif special == 0x3C:
                usage.pipeline, usage.latency = 'ialu', 1
                usage.delays_branch = True
                usage.add_vf_read(vf_s, lane((lower >> 21) & 3))
                usage.write_vi(vi_t)
            elif special == 0x3D:
                usage.pipeline, usage.latency = 'fmac', 4
                usage.read_vi(vi_s)
                usage.add_vf_write(vf_t, dest)
            elif special == 0x3E:
                usage.pipeline, usage.latency = 'lsu', 4
                usage.read_vi(vi_s)
                usage.write_vi(vi_t)
            elif special == 0x3F:
                usage.pipeline, usage.latency = 'lsu', 1
                usage.read_vi(vi_s)
                usage.read_vi(vi_t)
            elif special in (0x68, 0x69):
                usage.pipeline, usage.latency = 'ialu', 1
                usage.write_vi(vi_t)
            elif special == 0x6C:
                usage.pipeline, usage.latency = 'xgkick', 2
                usage.read_vi(vi_s)
            else:
                # R register, MFP, EFU and WAITP are not translated.
                usage.reserved = True
    else:
        usage.reserved = True
    return usage


BRANCH_OPS = {0x20, 0x21, 0x24, 0x25, 0x28, 0x29, 0x2C, 0x2D, 0x2E, 0x2F}
MAC_READS = {0x18, 0x1A, 0x1B}            # FMEQ / FMAND / FMOR
STATUS_USES = {0x14, 0x15, 0x16, 0x17}    # FSEQ / FSSET / FSAND / FSOR

# FMAC operations (upper opcode, or upper special for the ACC forms): (operation, operand).
FMAC_TABLE = {}
for _base, _kind in ((0x00, 'add'), (0x04, 'sub'), (0x08, 'madd'), (0x0C, 'msub'), (0x18, 'mul')):
    for _bc in range(4):
        FMAC_TABLE[_base + _bc] = (_kind, 'bc')
FMAC_TABLE.update({0x1C: ('mul', 'q'), 0x1E: ('mul', 'i'), 0x20: ('add', 'q'), 0x21: ('madd', 'q'),
                   0x22: ('add', 'i'), 0x23: ('madd', 'i'), 0x24: ('sub', 'q'), 0x25: ('msub', 'q'),
                   0x26: ('sub', 'i'), 0x27: ('msub', 'i'), 0x28: ('add', 'v'), 0x29: ('madd', 'v'),
                   0x2A: ('mul', 'v'), 0x2C: ('sub', 'v'), 0x2D: ('msub', 'v'), 0x2E: ('op', 'v')})


def flag_fmac(upper):
    """True when the upper word is an FMAC operation that writes MAC and status (Exec::fmacVec)."""
    if DEST(upper) == 0:
        return False
    op = upper & 0x3F
    if op >= 0x3C:
        special = upper_special(upper)
        if special in (0x1D, 0x1F, 0x2F, 0x30) or 0x10 <= special <= 0x17:
            return False
        return special in FMAC_TABLE
    if 0x10 <= op <= 0x17 or op in (0x1D, 0x1F, 0x2B, 0x2F):
        return False
    return op in FMAC_TABLE


class Pair:
    def __init__(self, image, pc):
        self.pc = pc
        self.lower = int.from_bytes(image[pc:pc + 4], 'little')
        self.upper = int.from_bytes(image[pc + 4:pc + 8], 'little')
        self.i_bit = bool(self.upper & 0x80000000)
        self.e_bit = bool(self.upper & 0x40000000)
        self.d_bit = bool(self.upper & 0x10000000)
        self.t_bit = bool(self.upper & 0x08000000)
        self.up = decode_upper(self.upper)
        self.lo = decode_lower(self.lower) if not self.i_bit else Usage()
        up_reg = self.up.vf_write[0]
        self.shadow = 0
        self.suppressed = 0
        if up_reg and (self.lo.vf_read_lanes(up_reg) or self.lo.vf_write[0] == up_reg):
            self.shadow = up_reg
            if self.lo.vf_write[0] == up_reg:
                self.suppressed = up_reg
        self.op_hi = (self.lower >> 25) & 0x7F
        self.is_branch = not self.i_bit and self.op_hi in BRANCH_OPS

    def branch_target(self):
        return (self.pc + 8 + IMM11(self.lower) * 8) & 0x3FFF


def analyse(image):
    """Reachable pairs from PC 0, MSCNT resume points and BAL/JALR return points."""
    size = len(image)
    if size != 16384:
        raise ValueError('Expected a complete 16 KiB VU1 image')
    pairs = {}

    def pair(pc):
        # Port Lab: micro-PC wraps, including E-bit/branch delay slots.
        pc &= 0x3FFF
        if pc not in pairs:
            pairs[pc] = Pair(image, pc)
        return pairs[pc]

    def check_slot(slot):
        if slot.is_branch or slot.e_bit:
            raise Reject(f'branch or E bit in a delay slot at {slot.pc:04x}')
        if slot.d_bit or slot.t_bit:
            raise Reject(f'D/T bit at {slot.pc:04x}')
        if slot.up.reserved or slot.lo.reserved:
            raise Reject(f'unsupported instruction at {slot.pc:04x}: {slot.upper:08x} {slot.lower:08x}')

    roots = [0]
    returns = set()
    seen = set()
    while roots:
        start = roots.pop()
        work = [start]
        while work:
            pc = work.pop()
            if pc in seen or pc + 8 > size:
                continue
            seen.add(pc)
            p = pair(pc)
            if p.d_bit or p.t_bit:
                raise Reject(f'D/T bit at {pc:04x}')
            if p.up.reserved or p.lo.reserved:
                raise Reject(f'unsupported instruction at {pc:04x}: {p.upper:08x} {p.lower:08x}')
            if p.e_bit:
                if p.is_branch:
                    raise Reject(f'branch with E bit at {pc:04x}')
                slot = pair(pc + 8)
                check_slot(slot)
                roots.append((pc + 16) & 0x3FFF)
                continue
            if p.is_branch:
                slot = pair(pc + 8)
                check_slot(slot)
                if p.op_hi in (0x21, 0x25):
                    ret = (pc + 16) & 0x3FFF
                    if ret not in returns:
                        returns.add(ret)
                        roots.append(ret)
                if p.op_hi in (0x24, 0x25):
                    continue  # targets are the BAL/JALR return points
                work.append(p.branch_target())
                if p.op_hi not in (0x20, 0x21):
                    work.append((pc + 16) & 0x3FFF)
                continue
            work.append((pc + 8) & 0x3FFF)
    labels = {pc: pairs[pc] for pc in sorted(seen)}
    return labels, pairs


# ----------------------------------------------------------------------------- independent entries

Q_UPPER = {0x1C, 0x20, 0x21, 0x24, 0x25}          # MULq, ADDq, MADDq, SUBq, MSUBq (and ACC forms)
I_UPPER = {0x1D, 0x1E, 0x1F, 0x22, 0x23, 0x26, 0x27}  # MAXi, MULi, MINIi, ADDi, MADDi, SUBi, MSUBi
I_SPECIAL = {0x1E, 0x22, 0x23, 0x26, 0x27}        # MULAi, ADDAi, MADDAi, SUBAi, MSUBAi
CLIP_LATENCY = 4
MAC_LATENCY = 4


def upper_reads_q(upper):
    op = upper & 0x3F
    return (upper_special(upper) in Q_UPPER) if op >= 0x3C else op in Q_UPPER


def upper_reads_i(upper):
    op = upper & 0x3F
    return (upper_special(upper) in I_SPECIAL) if op >= 0x3C else op in I_UPPER


def clip_bits_read(p):
    """Bits of the clip flag register the pair's lower instruction reads (0: none)."""
    if p.i_bit:
        return 0
    imm = p.lower & 0xFFFFFF
    if p.op_hi == 0x10:          # FCEQ: all 24 bits
        return 0xFFFFFF
    if p.op_hi == 0x12:          # FCAND: the bits of the mask
        return imm
    if p.op_hi == 0x13:          # FCOR: the bits the mask leaves out
        return ~imm & 0xFFFFFF
    if p.op_hi == 0x1C:          # FCGET: bits 0-11
        return 0xFFF
    return 0


def entry_inputs(labels, all_pairs, entry, limit=300000, metrics=None):
    """The state left by earlier runs that a run from `entry` reads, as a set of keys
    ('vf', reg, lane), ('vi', reg), ('acc', lane), ('q',), ('i',), ('mac',), ('status',)
    and ('clip', bits); None when the search gives up. A value counts as the run's own once
    it wrote it: flag and Q writes only once their latency has passed (reads do not wait
    for them); a branch reads the VI value from before the pair ahead of it (the integer-op
    quirk); status reads always count (the sticky bits carry over). Given the same inputs a
    run writes the same values whatever the rest of the state was, so the runtime may start
    it on a copy of VU memory before the run ahead of it ends and check the inputs later."""
    returns = tuple(sorted({(q.pc + 16) & 0x3FFF for q in labels.values()
                            if q.is_branch and q.op_hi in (0x21, 0x25)}))
    # State: pc, written keys, pending delay-slot targets, (VI reg, written before) of the
    # pair before for the branch quirk, Q, MAC ('in', 'own' or pairs left), clip (CLIPs
    # landed, 4 once the earlier flags are all shifted out or replaced; ((pairs left,
    # shifts), ...) in flight, FCSET counting 4).
    inputs = set()
    clip_inputs = 0
    seen = set()
    def finish(result):
        if metrics is not None:
            metrics['visited_states'] = len(seen)
        return result
    work = [(entry, frozenset(), None, None, 'in', 'in', (0, ()))]
    while work:
        state = work.pop()
        if state in seen:
            continue
        if len(seen) >= limit:
            return finish(None)
        seen.add(state)
        pc, written, slot_targets, vb, q, mac, clip = state
        p = all_pairs.get(pc)
        if p is None:
            return finish(None)

        # Writes whose latency has passed by this pair.
        if isinstance(q, int):
            q = 'own' if q <= 1 else q - 1
        if isinstance(mac, int):
            mac = 'own' if mac <= 1 else mac - 1
        shifts, pending = clip
        still = []
        for left, count in pending:
            if left <= 1:
                shifts = min(shifts + count, 4)
            else:
                still.append((left - 1, count))
        pending = tuple(still)

        # Reads.
        for reg, lanes in p.up.vf_read + p.lo.vf_read:
            if reg:
                inputs.update(('vf', reg, c) for c in comps(lanes) if ('vf', reg, c) not in written)
        vi_reads = (p.up.vi_read | p.lo.vi_read) & 0xFFFE
        for reg in range(1, 16):
            if vi_reads & (1 << reg):
                if p.is_branch and vb is not None and vb[0] == reg:
                    if not vb[1]:
                        inputs.add(('vi', reg))
                elif ('vi', reg) not in written:
                    inputs.add(('vi', reg))
        inputs.update(('acc', c) for c in comps(p.up.acc_read | p.lo.acc_read) if ('acc', c) not in written)
        if upper_reads_q(p.upper) and q != 'own':
            inputs.add(('q',))
        if upper_reads_i(p.upper) and ('i',) not in written:
            inputs.add(('i',))
        if not p.i_bit and p.op_hi in MAC_READS and mac != 'own':
            inputs.add(('mac',))
        if not p.i_bit and p.op_hi in (0x14, 0x16, 0x17):
            inputs.add(('status',))
        # Each CLIP shifted the flags left by six: what is left of the earlier flags sits
        # above the run's own bits, and stands for their low bits.
        if shifts < 4:
            clip_inputs |= (clip_bits_read(p) >> (6 * shifts)) & 0xFFFFFF

        # Writes.
        keys = set()
        reg, lanes = p.up.vf_write
        if reg:
            keys.update(('vf', reg, c) for c in comps(lanes))
        reg, lanes = p.lo.vf_write
        if reg and p.suppressed != reg:
            keys.update(('vf', reg, c) for c in comps(lanes))
        vi_writes = p.lo.vi_write & 0xFFFE
        keys.update(('vi', reg) for reg in range(1, 16) if vi_writes & (1 << reg))
        keys.update(('acc', c) for c in comps(p.up.acc_write))
        if p.i_bit:
            keys.add(('i',))
        first_vi = next((reg for reg in range(1, 16) if vi_writes & (1 << reg)), 0)
        next_vb = (first_vi, ('vi', first_vi) in written) if first_vi and p.lo.delays_branch else None
        written = written | keys if keys - written else written
        if p.lo.pipeline == 'fdiv' and not p.lo.wait_q:
            q = p.lo.latency     # issued after the one in flight, which has landed by then
        elif p.lo.wait_q and isinstance(q, int):
            q = 'own'
        if flag_fmac(p.upper) and mac == 'in':
            mac = MAC_LATENCY
        if p.up.writes_clip:
            pending = pending + ((CLIP_LATENCY, 1),)
        if not p.i_bit and p.op_hi == 0x11:   # FCSET
            pending = pending + ((CLIP_LATENCY, 4),)
        clip = (shifts, pending)

        # Control flow.
        if slot_targets is not None:
            for target in slot_targets:
                if target is not None:
                    work.append((target, written, None, next_vb, q, mac, clip))
            continue
        nxt = (pc + 8) & 0x3FFF
        if p.e_bit:
            work.append((nxt, written, (None,), next_vb, q, mac, clip))
        elif p.is_branch:
            if p.op_hi in (0x24, 0x25):
                targets = returns
            else:
                targets = (p.branch_target(),) if p.op_hi in (0x20, 0x21) else \
                    (p.branch_target(), (pc + 16) & 0x3FFF)
            work.append((nxt, written, targets, next_vb, q, mac, clip))
        else:
            work.append((nxt, written, None, next_vb, q, mac, clip))
    if clip_inputs:
        inputs.add(('clip', clip_inputs))
    return finish(inputs)


def run_entries(labels, all_pairs):
    """{entry: inputs} for the MSCAL/MSCNT entry points (PC 0 and the pairs after E-bit
    delay slots) that a run may start from ahead of time (entry_inputs). None for a program
    with FSSET, whose sticky status bits a later merge could not rebuild."""
    if any(not q.i_bit and q.op_hi == 0x15 for q in all_pairs.values()):
        return None
    roots = {0} | {(pc + 16) & 0x3FFF for pc, q in labels.items() if q.e_bit}
    result = {}
    for entry in sorted(pc for pc in roots if pc in labels):
        inputs = entry_inputs(labels, all_pairs, entry)
        if inputs is not None:
            result[entry] = inputs
    return result




def comps(mask):
    return [c for c in range(4) if mask & lane(c)]
