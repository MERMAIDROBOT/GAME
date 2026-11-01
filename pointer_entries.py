"""Optional pre-build scan for function starts that are reached only through pointers.

Without a symbol table the analyzer can merge a function into its predecessor after `jr ra`.
Before the first build, collect every pointer into executable file bytes (aligned data words, and
code `lui`+`addiu`/`ori` address pairs) and keep targets that look like function starts:

* the target follows `jr ra`, its delay slot and only alignment `nop`s, or
* the target opens with a stack prologue `addiu sp,sp,-N`.

Accepted targets become `general.entry_points` hints in the private analyzer config. Hints are
compilation candidates, not verified functions; `collect-runtime-targets` covers the stragglers.
"""
from pathlib import Path
import re
import struct
import tomllib

from feedback import config_with_hints, digest, executable_spans

JR_RA = 0x03E00008
MAX_PADDING_WORDS = 15
PAIR_WINDOW = 8
MAX_HINTS = 65536
# I-type opcodes whose rt is a destination: ALU immediates and loads (lb..lwu, ldl/ldr, lq, ld).
WRITES_RT = frozenset([*range(0x08, 0x10), *range(0x20, 0x28), 0x1A, 0x1B, 0x1E, 0x37])


def _is_prologue(word):
    # addiu sp, sp, imm with a negative immediate
    return word >> 16 == 0x27BD and word & 0x8000


def _load_segments(raw):
    h = struct.unpack_from('<HHIIIIIHHHHHH', raw, 16)
    off, size, count = h[4], h[8], h[9]
    for i in range(count):
        kind, pos, addr, _, filesz, _, flags, _ = struct.unpack_from('<8I', raw, off + size * i)
        if kind == 1 and filesz:
            yield addr, pos, filesz, bool(flags & 1)


def _pointers(raw, spans):
    inside = lambda v: not v & 3 and any(a <= v < b for a, b, _ in spans)
    found = set()
    for addr, pos, filesz, executable in _load_segments(raw):
        words = struct.unpack_from(f'<{filesz // 4}I', raw, pos)
        found.update(v for v in words if inside(v))
        if not executable:
            continue
        for i, word in enumerate(words):
            if word >> 26 != 0x0F:  # lui
                continue
            reg, high = (word >> 16) & 31, (word & 0xFFFF) << 16
            for follow in words[i + 1:i + 1 + PAIR_WINDOW]:
                op, rs, rt = follow >> 26, (follow >> 21) & 31, (follow >> 16) & 31
                if rs == reg and op in (0x09, 0x0D):  # addiu / ori from the lui register
                    low = follow & 0xFFFF
                    value = (high + (low - 0x10000 if op == 0x09 and low & 0x8000 else low)) & 0xFFFFFFFF
                    if inside(value):
                        found.add(value)
                if (op == 0 and (follow >> 11) & 31 == reg) or (op in WRITES_RT and rt == reg):
                    break  # the lui register was overwritten
    return found


def classify(raw, target, spans):
    a, _, pos = next((a, b, p) for a, b, p in spans if a <= target < b)
    word = lambda at: struct.unpack_from('<I', raw, pos + at - a)[0] if a <= at else None
    if _is_prologue(word(target)):
        return 'stack_prologue'
    for padding in range(MAX_PADDING_WORDS + 1):
        at = target - 4 * (padding + 2)
        if at < a:
            break
        if word(at) == JR_RA:
            return 'after_return'
        if word(target - 4 * (padding + 1)) != 0:
            break
    return None


def scan(raw):
    """Sorted [(address, reason)] of pointer-only function start candidates in an ELF image."""
    spans = executable_spans(raw)
    accepted = []
    for target in sorted(_pointers(raw, spans)):
        reason = classify(raw, target, spans)
        if reason:
            accepted.append((target, reason))
    if len(accepted) > MAX_HINTS:
        raise ValueError('Too many pointer-entry candidates')
    return accepted


def known_starts(private, config_text):
    """Addresses already named as entry points, or translated by an earlier run (file name suffix)."""
    entries = tomllib.loads(config_text).get('general', {}).get('entry_points', [])
    known = {int(m.group(1), 16) for e in entries if isinstance(e, str) and (m := re.search(r'@0x([0-9A-Fa-f]{1,8})$', e))}
    generated = Path(private) / 'generated'
    if generated.is_dir():
        known.update(int(m.group(1), 16) for p in generated.glob('*.cpp') if (m := re.search(r'_0x([0-9a-f]{1,8})\.cpp$', p.name)))
    return known


def apply(profile, private, write_config=True):
    """Write analysis/pointer-entry-hints.json and merge the hints into analysis/recomp.toml."""
    import pipeline as lab
    private = Path(private).resolve()
    config = private / 'analysis/recomp.toml'
    if not config.is_file():
        raise ValueError('Run analyze first; pointer hints extend its configuration')
    elf = lab.boot_executable(private / 'disc-root', profile)
    raw = elf.read_bytes()
    if profile.get('expected_sha256') and digest(raw) != profile['expected_sha256']:
        raise ValueError('Executable revision differs from profile')
    text = config.read_text()
    known = known_starts(private, text)
    found = scan(raw)
    candidates = [(at, reason) for at, reason in found if at not in known]
    hints = [f'ptr_{at:08X}@0x{at:08X}' for at, _ in candidates]
    rendered = config_with_hints(text, hints)
    report = dict(schema=1, title_id=profile['id'], executable_sha256=digest(raw),
                  implementation_sha256=digest(Path(__file__).read_bytes()),
                  analyzer_config_sha256=digest(text.encode()), local_only=True, functions_verified=False,
                  already_known=len(found) - len(candidates),
                  counts={r: sum(1 for _, x in candidates if x == r) for r in ('after_return', 'stack_prologue')},
                  candidates=[dict(address=f'{at:08x}', reason=r) for at, r in candidates],
                  config_updated=bool(write_config and rendered != text))
    if write_config and rendered != text:
        original = config.with_name('recomp.toml.analyzer-original')
        if not original.exists():
            original.write_text(text)
        config.write_text(rendered)
    lab.save(private / 'analysis/pointer-entry-hints.json', report)
    print(f'{len(candidates)} pointer-only entry candidates'
          + ('; merged into analysis/recomp.toml' if report['config_updated'] else ''))
    return report
