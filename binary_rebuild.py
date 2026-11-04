"""Split a PS2 executable into assembly the engine can rebuild byte-for-byte, and rebuild it.

split():   ELF -> one assembly file per loaded section (instructions with labels and symbolic references:
           branch/jump labels, %hi/%lo address pairs, %gp_rel, data pointers), a layout of ELF headers, and the
           bytes outside loaded sections (debug/symbol tables, padding) kept as pinned blobs.
assemble(): assembly + layout -> ELF. A rebuild identical to the disc's executable proves that every loaded byte
           is accounted for by the assembly text; later work replaces functions with source that must keep it so.

Generic: no title addresses. Outputs hold retail-derived code and stay in private storage.
"""
from pathlib import Path
import hashlib
import json
import re
import struct

import isa_r5900 as isa

LABEL = re.compile(r'[A-Za-z_.$][\w.$]*\Z')
EHDR = '<16sHHIIIIIHHHHHH'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


# ---- ELF ----------------------------------------------------------------------------------------------------
def parse_elf(raw):
    if raw[:6] != b'\x7fELF\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    header = list(struct.unpack_from(EHDR, raw, 0))
    ident, etype, machine, version, entry, phoff, shoff, flags, ehsize, phes, phn, shes, shn, shstr = header
    if machine != 8:
        raise ValueError('Expected MIPS ELF')
    phdrs = [list(struct.unpack_from('<8I', raw, phoff + i * phes)) for i in range(phn)]
    shdrs = [list(struct.unpack_from('<10I', raw, shoff + i * shes)) for i in range(shn)]
    names = []
    if shn:
        base = shdrs[shstr][4]
        for s in shdrs:
            end = raw.index(b'\0', base + s[0])
            names.append(raw[base + s[0]:end].decode('latin-1'))
    return dict(header=header, phdrs=phdrs, shdrs=shdrs, names=names)


def symbols(raw, elf):
    """[(address, name, type)] from .symtab, if present."""
    result = []
    for index, s in enumerate(elf['shdrs']):
        if s[1] != 2:
            continue
        strings = elf['shdrs'][s[6]]
        for offset in range(s[4], s[4] + s[5], 16):
            name_off, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', raw, offset)
            if not name_off or shndx == 0 or shndx >= 0xff00:
                continue
            start = strings[4] + name_off
            name = raw[start:raw.index(b'\0', start)].decode('latin-1')
            result.append((value, name, info & 15))
    return result


HEADER_SUFFIXES = ('.h', '.hh', '.hpp', '.hxx', '.inl')


def mdebug_units(raw, elf):
    """[(text address, source file)] for translation units in an ECOFF .mdebug table (headers excluded)."""
    if '.mdebug' not in elf['names']:
        return []
    offset = elf['shdrs'][elf['names'].index('.mdebug')][4]
    header = struct.unpack_from('<hH23I', raw, offset)
    if header[0] != 0x7009:
        return []
    strings, count, table = header[16], header[19], header[20]
    units = {}
    for i in range(count):
        f = struct.unpack_from('<10I2H7I', raw, table + i * 72)
        address, name_offset, string_base, procedures = f[0], f[1], f[2], f[11]
        at = strings + string_base + name_offset
        name = raw[at:raw.index(b'\0', at)].decode('latin-1')
        if procedures and not name.lower().endswith(HEADER_SUFFIXES):
            units.setdefault(address, name)
    return sorted(units.items())


def crt0_gp(raw, elf):
    """gp the entry code passes to SetupThread (syscall 0x3c in a0), from lui/addiu/ori constants."""
    entry = elf['header'][4]
    section = next((s for s in elf['shdrs'] if s[2] & 4 and s[3] <= entry < s[3] + s[5]), None)
    if section is None:
        return None
    known = {0: 0}
    for n in range(256):
        at = section[4] + entry - section[3] + 4 * n
        if at + 4 > section[4] + section[5]:
            break
        row = isa.decode(struct.unpack_from('<I', raw, at)[0])
        if row is None:
            continue
        spec, v = row
        name = spec.mnemonic
        if name == 'lui':
            known[v['rt']] = v['uimm'] << 16
        elif name in ('addiu', 'daddiu') and v['rs'] in known:
            known[v['rt']] = (known[v['rs']] + isa.sign(v['simm'], 16)) & 0xffffffff
        elif name == 'ori' and v['rs'] in known:
            known[v['rt']] = known[v['rs']] | v['uimm']
        elif name in ('or', 'daddu', 'addu') and v['rt'] == 0 and v['rs'] in known:
            known[v['rd']] = known[v['rs']]
        elif name == 'syscall' and known.get(3) == 0x3c:
            return known.get(4)
        else:
            written = _writes(spec, v)
            if written:                       # register zero is never written
                known.pop(written, None)
    return None


def unit_path(name, used):
    clean = re.sub(r'^[A-Za-z]:', '', name).replace('\\', '/')
    parts = [x for x in clean.split('/') if x not in ('', '.', '..')]
    path = '/'.join(re.sub(r'[^A-Za-z0-9_.+-]', '_', x) for x in parts) or 'unit'
    candidate, k = path, 1
    while candidate in used:
        k += 1; candidate = f'{path}~{k}'
    used.add(candidate)
    return candidate


# ---- split --------------------------------------------------------------------------------------------------
class Labels:
    def __init__(self, regions, code_regions=()):
        self.regions = regions            # [(start, end)] of loaded sections, end inclusive for labels
        self.code_regions = code_regions  # labels inside code must sit on instruction boundaries
        self.names = {}                   # address -> [names]
        self.used = set()
        self.printed = set()

    def inside(self, address):
        if any(start <= address < end for start, end in self.code_regions) and address % 4:
            return False
        return any(start <= address <= end for start, end in self.regions)

    def add(self, address, name):
        if not LABEL.match(name) or name in self.used:
            return False
        self.names.setdefault(address, []).append(name); self.used.add(name)
        return True

    def get(self, address, prefix):
        if address not in self.names:
            self.add(address, f'{prefix}_{address:08X}')
        return self.names[address][0]


def _writes(spec, values):
    """GPR an instruction writes, or None (first GPR operand of non-store, non-branch forms)."""
    if not spec.operands or spec.mnemonic.startswith(('s', 'b', 'j', 't', 'cache', 'pref', 'mt', 'c', 'qmt')) \
            and spec.mnemonic not in ('sll', 'srl', 'sra', 'sllv', 'srlv', 'srav', 'slt', 'sltu', 'slti',
                                      'sltiu', 'sub', 'subu', 'cfc1', 'cfc2'):
        return None
    first = spec.operands[0]
    if first in ('rd', 'rt') and isa.OPERANDS[first][1] == 'gpr':
        return values[first]
    return None


def _lo_use(row, reg, high):
    spec, values = row
    if spec.mnemonic not in LO_USERS:
        return None
    source, low, operand = ((values['rs'], values['simm'], 'simm') if spec.mnemonic in ('addiu', 'daddiu')
                            else (values['mem'][1], values['mem'][0], 'mem'))
    return (operand, ((high << 16) + isa.sign(low, 16)) & 0xffffffff) if source == reg else None


def _control(spec):
    return spec.mnemonic in ('jr', 'jalr') or any(isa.OPERANDS[o][1] in ('branch', 'jump') for o in spec.operands)


def _targets(base, m, spec, values):
    result = []
    for operand in spec.operands:
        kind = isa.OPERANDS[operand][1]
        if kind == 'branch':
            result.append(m + 1 + isa.sign(values[operand], 16))
        elif kind == 'jump' and spec.mnemonic == 'j':
            result.append(((((base + 4 * m + 4) & 0xf0000000) | (values[operand] << 2)) - base) // 4)
    return result


def _lo_users(rows, base, n, reg, high, function_starts, budget=8192):
    """Instructions that apply a %lo to the high half a lui put in reg: as a load/store base or addiu source,
    directly or through an addu/daddu of reg with an index. Follows the function's control flow (both branch paths,
    delay slots, in-function jumps); a path ends where the carrying registers are all replaced, at jr/eret, or
    after a call for caller-saved registers. A lui in a delay slot continues where its branch goes."""
    users, seen = {}, set()
    start = [n + 1]
    if n and rows[n - 1] is not None and _control(rows[n - 1][0]):
        spec, values = rows[n - 1]
        if spec.mnemonic in ('jr', 'jalr') or spec.mnemonic in CALLS:
            start = [] if spec.mnemonic == 'jr' or reg in CALLER_SAVED else [n + 1]
        else:
            start = _targets(base, n - 1, spec, values) + ([] if spec.mnemonic == 'j' else [n + 1])
    work = [(m, frozenset((reg,))) for m in start]

    def visit(m, carriers):
        """Record a use at m; return the carriers after m (empty: path ends)."""
        spec, values = rows[m]
        name = spec.mnemonic
        if name in LO_USERS:
            source, low, operand = ((values['rs'], values['simm'], 'simm') if name in ('addiu', 'daddiu')
                                    else (values['mem'][1], values['mem'][0], 'mem'))
            if source in carriers:
                users[m] = (m, operand, ((high << 16) + isa.sign(low, 16)) & 0xffffffff)
        written = _writes(spec, values)
        after = set(carriers)
        if name in ('addu', 'daddu') and (values['rs'] in carriers) != (values['rt'] in carriers):
            after.add(values['rd'])
        elif written is not None:
            after.discard(written)
        return frozenset(after)

    while work and len(seen) < budget:
        m, carriers = work.pop()
        if (m, carriers) in seen or m < 0 or m >= len(rows) or base + 4 * m in function_starts or not carriers:
            continue
        seen.add((m, carriers))
        if rows[m] is None:
            continue
        spec, values = rows[m]
        carriers = visit(m, carriers)
        if not carriers or spec.mnemonic == 'eret':
            continue
        if not _control(spec):
            work.append((m + 1, carriers))
            continue
        d = m + 1                                     # the delay slot runs on every path
        if d < len(rows) and rows[d] is not None:
            carriers = visit(d, carriers)
            if not carriers:
                continue
        name = spec.mnemonic
        if name == 'jr':
            continue
        if name in CALLS:
            kept = carriers - CALLER_SAVED
            if kept:
                work.append((m + 2, frozenset(kept)))
            continue
        work.extend((t, carriers) for t in _targets(base, m, spec, values))
        if name != 'j':
            work.append((m + 2, carriers))
    return sorted(users.values())

CALLS = {'jal', 'jalr', 'bltzal', 'bgezal', 'bltzall', 'bgezall'}
CALLER_SAVED = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 31}
LO_USERS = {'addiu', 'daddiu', 'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld', 'lq', 'sb', 'sh', 'sw', 'sd', 'sq',
            'lwc1', 'swc1', 'lqc2', 'sqc2'}


def split(raw, out, gp=None, extra_symbols=()):
    out = Path(out)
    if out.exists():
        raise ValueError('Choose a new output directory')
    elf = parse_elf(raw)
    loaded = [i for i, s in enumerate(elf['shdrs']) if s[2] & 2 and s[5]]     # SHF_ALLOC
    regions = [(elf['shdrs'][i][3], elf['shdrs'][i][3] + elf['shdrs'][i][5]) for i in loaded]
    code_regions = [(elf['shdrs'][i][3], elf['shdrs'][i][3] + elf['shdrs'][i][5]) for i in loaded
                    if elf['shdrs'][i][2] & 4]
    labels = Labels(regions, code_regions)
    table = symbols(raw, elf)
    for address, name, kind in sorted(table, key=lambda x: (x[2] != 2, x[0], x[1])):
        if kind in (1, 2) and labels.inside(address):
            labels.add(address, name)
    for address, name in extra_symbols:
        labels.add(address, name)
    if gp is None:
        gp = next((a for a, n, k in table if n == '_gp'), None)
    if gp is None:
        gp = crt0_gp(raw, elf)
    units = mdebug_units(raw, elf)

    # Decode every executable section once.
    code = {}
    for i in loaded:
        s = elf['shdrs'][i]
        if s[1] == 1 and s[2] & 4 and not elf['names'][i].startswith('.vu'):
            words = struct.unpack_from(f'<{s[5] // 4}I', raw, s[4])
            code[i] = [isa.decode(w) for w in words]

    # Symbolic references: (section, index) -> {operand: label expression}
    refs = {}
    counts = dict(branch=0, jump=0, hilo=0, gp_rel=0, data_pointer=0, undecoded=0, instructions=0, data_words=0)
    function_starts = {a for a, n, k in table if k == 2}
    for i, rows in code.items():                    # call targets start functions too (stripped executables)
        base = elf['shdrs'][i][3]
        for n, row in enumerate(rows):
            if row and row[0].mnemonic == 'jal':
                function_starts.add(((base + 4 * n + 4) & 0xf0000000) | (row[1]['jt'] << 2))
    for i, rows in code.items():
        base = elf['shdrs'][i][3]
        for n, row in enumerate(rows):
            if row is None:
                continue
            spec, values = row
            pc = base + 4 * n
            for operand in spec.operands:
                kind = isa.OPERANDS[operand][1]
                if kind == 'branch':
                    target = pc + 4 + (isa.sign(values[operand], 16) << 2)
                    if labels.inside(target):
                        refs.setdefault((i, n), {})[operand] = labels.get(target, '.L'); counts['branch'] += 1
                elif kind == 'jump':
                    target = ((pc + 4) & 0xf0000000) | (values[operand] << 2)
                    if labels.inside(target):
                        refs.setdefault((i, n), {})[operand] = labels.get(target, 'func'); counts['jump'] += 1
                elif kind == 'mem' and gp is not None and values[operand][1] == 28 and spec.mnemonic in LO_USERS:
                    target = gp + isa.sign(values[operand][0], 16)
                    if labels.inside(target):
                        refs.setdefault((i, n), {})[operand] = ('%gp_rel', labels.get(target, 'D'))
                        counts['gp_rel'] += 1
            if spec.mnemonic == 'addiu' and gp is not None and values['rs'] == 28:
                target = gp + isa.sign(values['simm'], 16)
                if labels.inside(target):
                    refs.setdefault((i, n), {})['simm'] = ('%gp_rel', labels.get(target, 'D')); counts['gp_rel'] += 1
            if spec.mnemonic == 'lui':
                reg, high = values['rt'], values['uimm']
                users = _lo_users(rows, base, n, reg, high, function_starts)
                if users and all(labels.inside(v) for _, _, v in users):
                    for m, operand, value in users:
                        refs.setdefault((i, m), {})[operand] = ('%lo', labels.get(value, 'D'))
                    refs.setdefault((i, n), {})['uimm'] = ('%hi', labels.get(users[0][2], 'D'))
                    counts['hilo'] += 1

    # Address-like high halves left as numbers: candidates for missing relocations (lui+ori builds constants).
    low_bound, high_bound = min(r[0] for r in regions) - 0x8000, max(r[1] for r in regions) + 0x8000
    counts.update(unpaired_address_like=0, constant_like=0)
    for i, rows in code.items():
        for n, row in enumerate(rows):
            if row and row[0].mnemonic == 'lui' and 'uimm' not in refs.get((i, n), {}) \
                    and low_bound <= row[1]['uimm'] << 16 <= high_bound:
                reg = row[1]['rt']
                constant = any(r and r[0].mnemonic in ('ori', 'xori', 'and', 'or', 'mtc1', 'dsll32')
                               and reg in (r[1].get('rs'), r[1].get('rt')) for r in rows[n + 1:n + 9])
                counts['constant_like' if constant else 'unpaired_address_like'] += 1

    # Data pointers in loaded data sections.
    data_refs = {}
    for i in loaded:
        s = elf['shdrs'][i]
        if s[1] != 1 or i in code:
            continue
        for offset in range(0, s[5] - 3, 4):
            if (s[3] + offset) % 4:
                continue
            value = struct.unpack_from('<I', raw, s[4] + offset)[0]
            if value >= 0x1000 and labels.inside(value):
                data_refs[s[3] + offset] = labels.get(value, 'D' if value not in function_starts else 'func')
                counts['data_pointer'] += 1

    # Emit assembly.
    (out / 'asm').mkdir(parents=True)
    header = list(elf['header']); header[0] = list(header[0])
    layout = dict(schema=1, header=header, phdrs=elf['phdrs'], sections=[], pieces=[], gp=gp,
                  file_size=len(raw), original_sha256=sha(raw))
    covered = []
    for index, s in enumerate(elf['shdrs']):
        entry = dict(name=elf['names'][index] if elf['names'] else '', header=s)
        if index in loaded and s[1] != 8:
            stem = f'asm/{index:02d}{re.sub(r"[^A-Za-z0-9_.]", "_", entry["name"])}'
            parts = []
            if index in code:
                bounds = [a for a, _ in units if s[3] < a < s[3] + s[5] and a % 4 == 0]
                names = dict(units)
                starts = [s[3]] + bounds
                used = set()
                for k, start in enumerate(starts):
                    end = starts[k + 1] if k + 1 < len(starts) else s[3] + s[5]
                    path = (f'{stem}/{unit_path(names[start], used)}.s' if start in names
                            else f'{stem}/{unit_path("startup" if k == 0 else f"unit_{start:08X}", used)}.s')
                    lines = [f'.section {entry["name"] or index} {start:#x}']
                    first, last = (start - s[3]) // 4, (end - s[3]) // 4
                    _emit_code(lines, raw, s, code[index], index, refs, labels, counts, first, last)
                    if k + 1 == len(starts):
                        _labels_at(lines, labels, s[3] + s[5])
                    (out / path).parent.mkdir(parents=True, exist_ok=True)
                    (out / path).write_text('\n'.join(lines) + '\n')
                    parts.append(path)
            else:
                path = stem + '.s'
                lines = [f'.section {entry["name"] or index} {s[3]:#x}']
                _emit_data(lines, raw, s, data_refs, labels, counts)
                _labels_at(lines, labels, s[3] + s[5])
                (out / path).write_text('\n'.join(lines) + '\n')
                parts.append(path)
            entry['assembly'] = parts
            covered.append((s[4], s[4] + s[5]))
        layout['sections'].append(entry)
    # Labels no section printed (inside uninitialised sections, or at their ends) are absolute definitions.
    printed = labels.printed
    absolute = [f'{name} = {address:#x}' for address, names in sorted(labels.names.items())
                for name in names if name not in printed]
    (out / 'asm/symbols.s').write_text('\n'.join(absolute) + '\n')
    layout['symbols'] = 'asm/symbols.s'
    header_size = elf['header'][8] + elf['header'][10] * elf['header'][9]
    covered.append((0, header_size))
    shoff, shes, shn = elf['header'][6], elf['header'][11], elf['header'][12]
    covered.append((shoff, shoff + shes * shn))
    # Everything else in the file is a blob (non-loaded sections, padding).
    covered.sort()
    position, blob = 0, 0
    (out / 'blobs').mkdir()
    for start, end in covered + [(len(raw), len(raw))]:
        if start > position:
            data = raw[position:start]
            piece = dict(offset=position, size=len(data), sha256=sha(data))
            if data.count(0) == len(data):
                piece['zero'] = True
            else:
                piece['blob'] = f'blobs/{blob:03d}.bin'; (out / piece['blob']).write_bytes(data); blob += 1
            layout['pieces'].append(piece)
        position = max(position, end)
    (out / 'layout.json').write_text(json.dumps(layout, indent=1) + '\n')
    counts['labels'] = sum(len(v) for v in labels.names.values())
    return counts


def _labels_at(lines, labels, address):
    for name in labels.names.get(address, ()):
        if name not in labels.printed:
            labels.printed.add(name)
            lines.append(f'{name}:')


def _operand_text(spec, values, operand, ref):
    kind = isa.OPERANDS[operand][1]
    value = values[operand]
    if ref is not None:
        if kind == 'mem':
            return f'{ref[0]}({ref[1]})({isa.GPR[value[1]]})'
        if isinstance(ref, tuple):
            return f'{ref[0]}({ref[1]})'
        return ref
    if kind == 'gpr':
        return isa.GPR[value]
    if kind == 'fpr':
        return f'f{value}'
    if kind == 'vf':
        return f'vf{value}'
    if kind == 'vi':
        return f'vi{value}'
    if kind == 's16':
        return str(isa.sign(value, 16))
    if kind == 's5':
        return str(isa.sign(value, 5))
    if kind == 'mem':
        return f'{isa.sign(value[0], 16)}({isa.GPR[value[1]]})'
    if kind == 'branch':
        return f'.{isa.sign(value, 16)}'                       # raw word offset, no label
    if kind == 'jump':
        return f'@{value:#x}'                                   # raw 26-bit target
    return hex(value) if kind == 'u16' else str(value)


def _emit_code(lines, raw, s, rows, index, refs, labels, counts, first=0, last=None):
    for n in range(first, len(rows) if last is None else last):
        row = rows[n]
        address = s[3] + 4 * n
        _labels_at(lines, labels, address)
        if row is None:
            lines.append(f'    .word {struct.unpack_from("<I", raw, s[4] + 4 * n)[0]:#010x}')
            counts['undecoded'] += 1
            continue
        spec, values = row
        counts['instructions'] += 1
        if spec.mnemonic == 'sll' and not any(values.values()):
            lines.append('    nop'); continue
        mine = refs.get((index, n), {})
        operands = ', '.join(_operand_text(spec, values, o, mine.get(o)) for o in spec.operands)
        lines.append(f'    {isa.mnemonic_text(spec, values)} {operands}'.rstrip())


def _emit_data(lines, raw, s, data_refs, labels, counts):
    start, size = s[3], s[5]
    offset = 0
    marks = sorted(a for a in labels.names if start <= a < start + size)
    k = 0
    while offset < size:
        address = start + offset
        _labels_at(lines, labels, address)
        while k < len(marks) and marks[k] <= address:
            k += 1
        next_mark = marks[k] if k < len(marks) else start + size
        if address % 4 == 0 and next_mark - address >= 4 and size - offset >= 4:
            run = []
            while address % 4 == 0 and next_mark - address >= 4 and size - offset >= 4 and len(run) < 8:
                if address in data_refs:
                    if run:
                        break
                    lines.append(f'    .word {data_refs[address]}'); counts['data_words'] += 1
                    offset += 4; address += 4
                    break
                run.append(struct.unpack_from('<I', raw, s[4] + offset)[0]); offset += 4; address += 4
                if start + offset in labels.names:
                    break
            if run:
                lines.append('    .word ' + ', '.join(f'{w:#x}' for w in run)); counts['data_words'] += len(run)
        else:
            stop = min(next_mark, start + size, (address | 3) + 1)
            data = raw[s[4] + offset:s[4] + offset + (stop - address)]
            lines.append('    .byte ' + ', '.join(str(b) for b in data))
            offset += len(data)


# ---- assemble -----------------------------------------------------------------------------------------------
def _int(text):
    return int(text, 0)


class Assembler:
    def __init__(self, gp):
        self.gp = gp
        self.symbols = {}

    def value(self, text):
        text = text.strip()
        if re.fullmatch(r'-?(0x[0-9a-fA-F]+|\d+)', text):
            return _int(text)
        if text not in self.symbols:
            raise ValueError('undefined symbol ' + text)
        return self.symbols[text]

    def immediate(self, text, kind):
        m = re.fullmatch(r'(%hi|%lo|%gp_rel)\((.+)\)', text.strip())
        if not m:
            v = _int(text)
            return v & 0xffff
        v = self.value(m.group(2))
        if m.group(1) == '%hi':
            return ((v + 0x8000) >> 16) & 0xffff
        if m.group(1) == '%lo':
            return v & 0xffff
        if self.gp is None:
            raise ValueError('%gp_rel without gp')
        delta = v - self.gp
        if not -0x8000 <= delta < 0x8000:
            raise ValueError('%gp_rel out of range')
        return delta & 0xffff

    def operand(self, kind, text, pc):
        text = text.strip()
        if kind == 'gpr':
            return isa.GPR_INDEX[text]
        if kind in ('fpr', 'vf', 'vi'):
            prefix = {'fpr': 'f', 'vf': 'vf', 'vi': 'vi'}[kind]
            if not text.startswith(prefix):
                raise ValueError('expected register ' + text)
            return int(text[len(prefix):])
        if kind == 'mem':
            m = re.fullmatch(r'(.*)\((\w+)\)', text)
            return (self.immediate(m.group(1), 's16'), isa.GPR_INDEX[m.group(2)])
        if kind in ('s16', 'u16'):
            return self.immediate(text, kind)
        if kind == 's5':
            return _int(text) & 31
        if kind == 'branch':
            if text.startswith('.') and re.fullmatch(r'\.-?\d+', text):
                return _int(text[1:]) & 0xffff
            delta = self.value(text) - (pc + 4)
            if delta % 4 or not -0x20000 <= delta < 0x20000:
                raise ValueError('branch out of range ' + text)
            return (delta >> 2) & 0xffff
        if kind == 'jump':
            if text.startswith('@'):
                return _int(text[1:])
            target = self.value(text)
            if (target & 0xf0000000) != ((pc + 4) & 0xf0000000) or target % 4:
                raise ValueError('jump target out of region ' + text)
            return (target >> 2) & 0x3ffffff
        return _int(text)

    @staticmethod
    def statements(path):
        for line in Path(path).read_text().splitlines():
            line = line.split('#', 1)[0].rstrip()
            if line:
                yield line

    def instruction(self, mnemonic, args, pc):
        if mnemonic == 'nop' and not args:
            return 0
        specs, suffix = isa.split_mnemonic(mnemonic)
        for spec in specs:
            if len(spec.operands) != len(args):
                continue
            values = dict(suffix)
            for operand, text in zip(spec.operands, args):
                values[operand] = self.operand(isa.OPERANDS[operand][1], text, pc)
            return isa.encode(spec, values)
        raise ValueError(f'no form of {mnemonic} takes {len(args)} operands')

    @staticmethod
    def split_args(text):
        return [a for a in (x.strip() for x in text.split(',')) if a] if text.strip() else []

    def section(self, path, define):
        """Pass 1 (define=True) records labels; pass 2 returns (address, bytes)."""
        out = bytearray(); start = pc = None
        for line in self.statements(path):
            if line.startswith('.section'):
                start = pc = _int(line.split()[-1]); continue
            if line.endswith(':') and not line.startswith(' '):
                if define:
                    self.symbols[line[:-1]] = pc
                continue
            word, _, rest = line.strip().partition(' ')
            if word == '.word':
                args = self.split_args(rest)
                if not define:
                    for a in args:
                        out += struct.pack('<I', self.value(a) & 0xffffffff)
                pc += 4 * len(args)
            elif word == '.byte':
                args = self.split_args(rest)
                if not define:
                    out += bytes(_int(a) for a in args)
                pc += len(args)
            else:
                if not define:
                    out += struct.pack('<I', self.instruction(word, self.split_args(rest), pc))
                pc += 4
        return start, bytes(out)


def assemble(directory):
    directory = Path(directory)
    layout = json.loads((directory / 'layout.json').read_text())
    asm = Assembler(layout.get('gp'))
    files = [s for s in layout['sections'] if 'assembly' in s]
    for line in Assembler.statements(directory / layout['symbols']):
        name, _, value = line.partition('=')
        asm.symbols[name.strip()] = _int(value.strip())
    for s in files:
        for part in s['assembly']:
            asm.section(directory / part, True)
    image = bytearray(layout['file_size'])
    written = bytearray(layout['file_size'])

    def put(offset, data):
        if any(written[offset:offset + len(data)]):
            raise ValueError(f'overlapping output at {offset:#x}')
        image[offset:offset + len(data)] = data
        written[offset:offset + len(data)] = b'\1' * len(data)

    header = layout['header']
    put(0, struct.pack(EHDR, bytes(header[0]), *header[1:]))
    for i, p in enumerate(layout['phdrs']):
        put(header[5] + i * header[9], struct.pack('<8I', *p))
    for i, s in enumerate(layout['sections']):
        put(header[6] + i * header[11], struct.pack('<10I', *s['header']))
    for s in files:
        address, data = None, b''
        for part in s['assembly']:
            start, chunk = asm.section(directory / part, False)
            if address is None:
                address = start
            elif start != address + len(data):
                raise ValueError(f'{part}: starts at {start:#x}, previous part ended at {address + len(data):#x}')
            data += chunk
        h = s['header']
        if address != h[3] or len(data) != h[5]:
            raise ValueError(f'{s["name"]}: assembled {len(data):#x} bytes at {address:#x}, expected {h[5]:#x} at {h[3]:#x}')
        put(h[4], data)
    for piece in layout['pieces']:
        data = bytes(piece['size']) if piece.get('zero') else (directory / piece['blob']).read_bytes()
        if sha(data) != piece['sha256']:
            raise ValueError('blob differs from layout: ' + str(piece.get('blob')))
        put(piece['offset'], data)
    if not all(written):
        raise ValueError('rebuilt file has uncovered bytes')
    return bytes(image)


def roundtrip(raw, out, gp=None):
    counts = split(raw, out, gp)
    rebuilt = assemble(out)
    report = dict(schema=1, original_sha256=sha(raw), rebuilt_sha256=sha(rebuilt), identical=rebuilt == raw,
                  bytes=len(raw), **counts)
    if not report['identical']:
        first = next(i for i, (a, b) in enumerate(zip(raw, rebuilt)) if a != b) if len(raw) == len(rebuilt) else None
        report['first_difference'] = first
    (Path(out) / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    return report


def run(profile, private, name, gp=None):
    """Pipeline entry: round-trip the title's boot executable into private storage."""
    import pipeline as lab
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('Invalid rebuild name')
    private = Path(private).resolve()
    out = private / 'binary-rebuild' / name
    raw = lab.boot_executable(private / 'disc-root', profile).read_bytes()
    out.parent.mkdir(parents=True, exist_ok=True)
    report = roundtrip(raw, out, gp)
    report.update(local_only=True, retail_derived=True, output=str(out))
    lab.save(private / 'analysis' / f'binary-rebuild-{name}.json', report)
    return report
