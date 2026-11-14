"""Find small SDK accessors in stripped executables by instruction shape.

Some SDK routines are too short for the analyzer's signature database to name,
yet the runtime must own them (their state lives in the runtime, not in guest
memory). Each rule below describes an adjacent PAIR of routines by decoded
fields, never by copied bytes: both members must match and agree on the shared
table address or structure offset. A lone look-alike never binds.

Rules come from the documented SDK ABI; no title addresses are embedded.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import tomllib

JR_RA = 0x03E00008


def _fields(w):
    return dict(op=w >> 26, rs=(w >> 21) & 31, rt=(w >> 16) & 31, rd=(w >> 11) & 31,
                sa=(w >> 6) & 31, fn=w & 63, imm=w & 0xFFFF)


def _simm(v):
    return v - 0x10000 if v & 0x8000 else v


A0, A1, V0, V1 = 4, 5, 2, 3


def _lw(w, rt, base):
    f = _fields(w)
    return f['op'] == 0x23 and f['rt'] == rt and f['rs'] == base


def _sif_table(words, i):
    """lui v0,HI; sll a0,a0,2; addiu v0,v0,LO; addu a0,a0,v0 -> table address."""
    if i + 4 > len(words):
        return None
    lui, sll, addiu, addu = (_fields(w) for w in words[i:i + 4])
    if not (lui['op'] == 0x0F and lui['rt'] == V0 and lui['rs'] == 0):
        return None
    if not (sll['op'] == 0 and sll['fn'] == 0 and sll['rt'] == A0 and sll['rd'] == A0 and sll['sa'] == 2):
        return None
    if not (addiu['op'] == 0x09 and addiu['rs'] == V0 and addiu['rt'] == V0):
        return None
    if not (addu['op'] == 0 and addu['fn'] == 0x21 and addu['rd'] == A0 and
            {addu['rs'], addu['rt']} == {A0, V0}):
        return None
    return ((lui['imm'] << 16) + _simm(addiu['imm'])) & 0xFFFFFFFF


def _sif_sreg_pair(words, i):
    """sceSifGetSreg(reg) returns table[reg]; sceSifSetSreg(reg, value) stores it."""
    table = _sif_table(words, i)
    if table is None or words[i + 4:i + 5] != [JR_RA] or i + 6 > len(words):
        return None
    if not (_lw(words[i + 5], V0, A0) and _fields(words[i + 5])['imm'] == 0):
        return None
    j = i + 6
    while j < len(words) and words[j] == 0:
        j += 1
    if _sif_table(words, j) != table or j + 7 > len(words):
        return None
    move, ret, store = (_fields(w) for w in words[j + 4:j + 7])
    # v0 = a1 (daddu/addu/or with zero), then jr ra; sw a1,0(a0)
    if not (move['op'] == 0 and move['fn'] in (0x2D, 0x21, 0x25) and move['rd'] == V0 and
            {move['rs'], move['rt']} == {A1, 0}):
        return None
    if words[j + 5] != JR_RA or not (store['op'] == 0x2B and store['rt'] == A1 and
                                     store['rs'] == A0 and store['imm'] == 0):
        return None
    return [('sceSifGetSreg', i), ('sceSifSetSreg', j)]


def _mpeg_end_pair(words, i):
    """sceMpegIsEnd: *(*(mp+K)); sceMpegIsRefBuffEmpty: (*(mp+K))[1] == 0."""
    if i + 3 > len(words):
        return None
    first = _fields(words[i])
    if not _lw(words[i], V1, A0) or words[i + 1] != JR_RA:
        return None
    if not (_lw(words[i + 2], V0, V1) and _fields(words[i + 2])['imm'] == 0):
        return None
    j = i + 3
    while j < len(words) and words[j] == 0:
        j += 1
    if j + 4 > len(words) or words[j] != words[i]:
        return None
    load, ret, test = words[j + 1], words[j + 2], _fields(words[j + 3])
    if not (_lw(load, V0, V1) and _fields(load)['imm'] == 4) or ret != JR_RA:
        return None
    if not (test['op'] == 0x0B and test['rs'] == V0 and test['rt'] == V0 and test['imm'] == 1):
        return None
    return [('sceMpegIsEnd', i), ('sceMpegIsRefBuffEmpty', j)]


RULES = (_sif_sreg_pair, _mpeg_end_pair)


def find(raw):
    """Return {name: address} for uniquely matched pairs in executable segments."""
    from feedback import executable_spans
    found = {}
    for start, end, offset in executable_spans(raw):
        count = (end - start) // 4
        words = list(struct.unpack_from(f'<{count}I', raw, offset))
        for rule in RULES:
            for i in range(count):
                pair = rule(words, i)
                if not pair:
                    continue
                for name, index in pair:
                    found.setdefault(name, set()).add(start + index * 4)
    # Ambiguity never binds: a name matched at two places is dropped.
    return {name: next(iter(at)) for name, at in found.items() if len(at) == 1}


def _array_span(text, key, start, finish):
    """Character span of a top-level `key = [...]` array inside [start, finish)."""
    match = re.compile(r'^' + key + r'[ \t]*=[ \t]*\[', re.M).search(text, start, finish)
    if not match:
        return None
    depth, i, quote = 0, match.end() - 1, None
    while i < finish:
        c = text[i]
        if quote:
            if c == '\\' and quote == '"':
                i += 2; continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif c == '#':
            i = text.find('\n', i)
            if i < 0:
                break
            continue
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return match.start(), i + 1
        i += 1
    raise ValueError('Unterminated ' + key + ' array')


def with_stubs(text, additions):
    """Append address-qualified stubs to general.stubs; every other value unchanged."""
    before = tomllib.loads(text)
    general = before.get('general')
    if not isinstance(general, dict):
        raise ValueError('Analyzer config has no general table')
    existing = general.get('stubs', [])
    merged = existing + [s for s in additions if s not in existing]
    header = re.search(r'^\[general\][ \t]*(?:#[^\n]*)?$', text, re.M)
    if not header:
        raise ValueError('Unsupported general table layout')
    end = re.search(r'^\[', text[header.end():], re.M)
    finish = header.end() + end.start() if end else len(text)
    span = _array_span(text, 'stubs', header.end(), finish)
    rendered = 'stubs = ' + json.dumps(merged)
    if span:
        result = text[:span[0]] + rendered + text[span[1]:]
    else:
        result = text[:header.end()] + '\n' + rendered + text[header.end():]
    after = tomllib.loads(result)
    expected = dict(before)
    expected['general'] = dict(general, stubs=merged)
    if after != expected:
        raise ValueError('Stub merge changed other configuration values')
    return result


def configuration(profile, private, config):
    """Return (config path, report); writes a derived config only when stubs are added."""
    import pipeline as lab
    private = Path(private)
    raw = lab.boot_executable(private / 'disc-root', profile).read_bytes()
    text = Path(config).read_text()
    general = tomllib.loads(text).get('general', {})
    present = general.get('stubs', [])
    taken_names = {s.split('@')[0] for s in present}
    taken_addresses = {int(s.split('@')[1], 16) for s in present if '@' in s}
    try:
        matched = find(raw)
    except ValueError as error:
        # Not a loadable MIPS ELF: nothing to match; translation reports its own errors.
        return config, dict(schema=1, matched={}, added=[], skipped=str(error))
    additions = [f'{name}@0x{at:08X}' for name, at in sorted(matched.items())
                 if name not in taken_names and at not in taken_addresses]
    report = dict(schema=1, matched={k: f'0x{v:08x}' for k, v in sorted(matched.items())},
                  added=additions, executable_sha256=hashlib.sha256(raw).hexdigest())
    if not additions:
        return config, report
    result = private / 'analysis/sdk-shape-recomp.toml'
    result.write_text(with_stubs(text, additions))
    lab.save(private / 'analysis/sdk-shape-bindings.json', report)
    return result, report
