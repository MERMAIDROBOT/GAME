"""Overlay translation: code the game loads at run time, translated as separate programs.

Many titles copy code from the disc into RAM while running (level programs, overlays). Several of
these programs can occupy the same addresses, so they cannot join the boot executable's function
table. This stage takes the EE code regions found by the disc code census, wraps each one at its
estimated load address in a minimal executable, runs the same analyzer and recompiler on it and
gives every generated symbol an overlay-qualified name. Each overlay gets its own dense function
table plus a content signature: words at addresses where it differs from every other overlay and
from the boot executable. At run time the dispatcher (runtime/overlays/overlay_dispatch.hpp) uses
an overlay's table only while its signature is present in guest memory, so the program that is
actually loaded is the one that runs.

Load addresses are estimates from call-target voting; overlays below the acceptance ratio are
reported and skipped, never guessed. Translation counts are evidence of translation, not of
correct execution.
"""
from pathlib import Path
import json
import re
import shutil
import struct

MIN_VOTE_RATIO = 0.25   # base votes / sampled call targets needed to trust an estimated load address
SIGNATURE_WORDS = 8     # distinguishing words per overlay
EM_MIPS, ET_EXEC, PT_LOAD = 8, 2, 1
SHF_ALLOC, SHF_EXECINSTR = 0x2, 0x4


BLOCK = 4096  # code-span granularity inside a census region


def code_spans(data, block=BLOCK, max_gap=1):
    """[(start, end)] byte spans of `data` that hold MIPS code: blocks with a `jr ra` or a stack
    prologue, joined across gaps of at most `max_gap` blocks. The rest of a region is data (tables,
    microcode, assets loaded beside the code) and must not be decoded as instructions."""
    marks = []
    for at in range(0, len(data) - len(data) % 4, block):
        words = struct.unpack_from(f'<{min(block, len(data) - at) // 4}I', data, at)
        returns = sum(1 for w in words if w == 0x03E00008)
        prologues = sum(1 for w in words if w & 0xFFFF8000 == 0x27BD8000)
        marks.append(returns >= 2 and prologues >= 1)
    spans, gap = [], 0
    for i, code in enumerate(marks):
        if code:
            if spans and gap <= max_gap and spans[-1][1] == (i - gap) * block:
                spans[-1][1] = min((i + 1) * block, len(data))
            else:
                spans.append([i * block, min((i + 1) * block, len(data))])
            gap = 0
        else:
            gap += 1
    return [tight for span in spans if (tight := _tighten(data, *span))]


def _tighten(data, start, end):
    """Shrink a block-aligned span to its first stack prologue and its last `jr ra` plus delay slot.

    A function that runs past the span's last block keeps going until its `jr ra` (or until a
    prologue shows that a new function began), so a block boundary never cuts an epilogue off."""
    words = struct.unpack_from(f'<{(end - start) // 4}I', data, start)
    first = next((i for i, w in enumerate(words) if w & 0xFFFF8000 == 0x27BD8000), None)
    last = next((i for i in range(len(words) - 1, -1, -1) if words[i] == 0x03E00008), None)
    if first is None or last is None or first >= last + 2:
        return None
    stop = start + 4 * last + 8
    for at in range(end, min(len(data) - len(data) % 4, end + BLOCK), 4):
        word = struct.unpack_from('<I', data, at)[0]
        if _is_prologue(word):
            break
        if word == 0x03E00008:
            stop = at + 8
            break
    return start + 4 * first, min(len(data), stop)


def _is_prologue(word):
    return word & 0xFFFF8000 == 0x27BD8000


def extend_spans(data, base, spans, rounds=8):
    """Add functions that code calls (`jal`) or data points to but that lie outside `spans`.

    Large functions can fill a block with too few returns or prologues for the density test. A call
    target or data pointer that lands on a stack prologue outside every span is code: it runs from
    there to its first `jr ra` plus delay slot. A call target needs no further evidence; a data word
    pointing outside the spans must land on a stack prologue. Repeats so newly found functions contribute calls."""
    words = struct.unpack_from(f'<{len(data) // 4}I', data)
    spans = sorted(spans)
    inside = lambda at: any(s <= at < e for s, e in spans)
    for _ in range(rounds):
        called = set()
        for s, e in spans:
            for i in range(s // 4, e // 4):
                if words[i] >> 26 == 3:
                    called.add(((base & 0xF0000000) | (words[i] & 0x03FFFFFF) << 2) - base)
        targets = set(called)
        targets.update(w - base for i, w in enumerate(words) if not inside(4 * i) and base <= w < base + len(data))
        added = []
        for at in sorted(targets):
            if at & 3 or not 0 <= at < len(data) or inside(at):
                continue
            if at not in called and not _is_prologue(words[at // 4]):
                continue  # a data word is weaker evidence than a call: require a prologue
            end = next((4 * i + 8 for i in range(at // 4, len(words)) if words[i] == 0x03E00008), None)
            if end is None:
                continue
            if any(s < min(end, len(data)) and at < e for s, e in spans + added):
                continue  # would overlap known code: not a clean function
            added.append((at, min(end, len(data))))
        if not added:
            break
        spans = sorted(spans + added)
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def wrap_elf(code, base, spans=None):
    """A minimal little-endian MIPS executable holding `code` at `base`.

    `spans` lists the byte ranges that hold code (default: everything). Each becomes an executable
    section and load segment; the bytes between them are data segments, still loaded so pointers
    and jump tables into them resolve but never decoded as instructions."""
    code = bytes(code) + bytes(-len(code) % 4)
    spans = [(0, len(code))] if spans is None else [(s, min(e, len(code))) for s, e in spans if s < e]
    pieces, covered = [], 0
    for start, end in spans:
        if start > covered:
            pieces.append((covered, start, False))
        pieces.append((start, end, True))
        covered = end
    if covered < len(code):
        pieces.append((covered, len(code), False))
    names, shstrtab = {}, bytearray(b'\x00')
    for name in ('.text', '.data', '.shstrtab'):
        names[name] = len(shstrtab)
        shstrtab += name.encode() + b'\x00'
    ehsize, phsize, shsize = 52, 32, 40
    text_offset = (ehsize + phsize * len(pieces) + 0xFFF) & ~0xFFF
    shstr_offset = text_offset + len(code)
    sh_offset = shstr_offset + len(shstrtab) + (-(shstr_offset + len(shstrtab)) % 4)
    programs, sections = b'', [bytes(shsize)]
    for start, end, executable in pieces:
        programs += struct.pack('<8I', PT_LOAD, text_offset + start, base + start, base + start,
                                end - start, end - start, 5 if executable else 6, 4)
        sections.append(struct.pack('<10I', names['.text' if executable else '.data'], 1,
                                    SHF_ALLOC | (SHF_EXECINSTR if executable else 0x1), base + start,
                                    text_offset + start, end - start, 0, 0, 4, 0))
    sections.append(struct.pack('<10I', names['.shstrtab'], 3, 0, 0, shstr_offset, len(shstrtab), 0, 0, 1, 0))
    entry = base + spans[0][0] if spans else base
    header = struct.pack('<4s5B7x2H5I6H', b'\x7fELF', 1, 1, 1, 0, 0, ET_EXEC, EM_MIPS, 1, entry, ehsize,
                         sh_offset, 0x20924001, ehsize, phsize, len(pieces), shsize, len(sections), len(sections) - 1)
    out = bytearray(header + programs)
    out += bytes(text_offset - len(out)) + code + shstrtab
    out += bytes(sh_offset - len(out)) + b''.join(sections)
    return bytes(out)


MAX_JOIN_GAP = 4 << 20  # bytes of non-code between two census regions of one program


def candidates(census):
    """Census EE regions with a trusted load address as (id, region) pairs, and rejected regions.

    Data inside one program can split it into several census regions. Neighbouring regions from the
    same source whose estimated load addresses differ exactly by their distance on disc are one
    program loaded in one piece, so they are joined (with the data between them)."""
    trusted, rejected = [], []
    for region in census.get('regions', []):
        if region.get('kind') != 'ee_code_candidate':
            continue
        base, votes, sampled = region.get('estimated_load_address'), region.get('base_votes', 0), region.get('sampled_call_targets', 0)
        if base is None or not sampled or votes / sampled < MIN_VOTE_RATIO:
            rejected.append(dict(offset=region['offset'], file=region['file'], reason='load address not established',
                                 base_votes=votes, sampled_call_targets=sampled))
            continue
        trusted.append(dict(region))
    trusted.sort(key=lambda r: (r['source'], r['file'], r['offset']))
    joined = []
    for region in trusted:
        if joined:
            last = joined[-1]
            distance = region['offset'] - last['offset']
            if (region['source'], region['file']) == (last['source'], last['file']) \
                    and int(region['estimated_load_address'], 16) - int(last['estimated_load_address'], 16) == distance \
                    and 0 <= region['offset'] - (last['offset'] + last['bytes']) <= MAX_JOIN_GAP:
                last['bytes'] = region['offset'] + region['bytes'] - last['offset']
                last['joined_regions'] = last.get('joined_regions', 1) + 1
                continue
        joined.append(region)
    return [(f"o{i:02d}", r) for i, r in enumerate(joined)], rejected


def signatures(programs, boot=None, count=SIGNATURE_WORDS):
    """{id: [(address, word)]}: code words where each program differs from every other program and the boot image.

    `programs` maps id -> (base, bytes, code spans); `boot` is (base, bytes) or None. Words come
    alternately from the first and the last code span so a partly overwritten program is not taken
    for resident. A program with fewer than `count` distinguishing words gets as many as exist.
    """
    def word(base, data, address):
        at = address - base
        return struct.unpack_from('<I', data, at)[0] if 0 <= at <= len(data) - 4 else None
    out = {}
    for pid, (base, data, spans) in programs.items():
        others = [(ob, od) for qid, (ob, od, _) in programs.items() if qid != pid] + ([boot] if boot else [])
        offsets = [at for s, e in spans for at in range(s, e - 3, 4)]
        order = [x for pair in zip(offsets, reversed(offsets)) for x in pair][:len(offsets)]
        chosen, seen = [], set()
        for at in order:
            if at in seen:
                continue
            seen.add(at)
            address, value = base + at, struct.unpack_from('<I', data, at)[0]
            if value and all(word(ob, od, address) != value for ob, od in others):
                chosen.append((address, value))
                if len(chosen) == count:
                    break
        out[pid] = sorted(chosen)
    return out


def declared_functions(generated):
    header = Path(generated) / 'ps2_recompiled_functions.h'
    return re.findall(r'^void (\w+)\(uint8_t\* rdram', header.read_text(), re.M)


TABLE_SYMBOLS = ('g_ps2RecompiledFunctionTableBase', 'g_ps2RecompiledFunctionTableEnd',
                 'g_ps2RecompiledFunctionTableSlotCount', 'g_ps2RecompiledFunctionTable')


def qualify(generated, overlay_id):
    """Rename every generated function and table symbol so overlays link beside the boot program."""
    generated = Path(generated)
    names = declared_functions(generated)
    renames = {n: f'{overlay_id}_{n}' for n in names}
    renames.update({s: s.replace('g_ps2Recompiled', f'g_ps2Overlay_{overlay_id}_', 1) for s in TABLE_SYMBOLS})
    pattern = re.compile(r'\b(' + '|'.join(sorted(map(re.escape, renames), key=len, reverse=True)) + r')\b')
    for path in sorted(generated.glob('*')):
        if path.suffix not in ('.cpp', '.h') or path.name.startswith('._'):
            continue
        text = path.read_text()
        if path.name == 'register_functions.cpp':
            # The runtime header declares the boot program's table; this file now defines its own.
            text = text.replace('#include "ps2_runtime.h"', '#include "ps2_runtime.h"\n'
                                f'extern const uint32_t g_ps2Overlay_{overlay_id}_FunctionTableBase, '
                                f'g_ps2Overlay_{overlay_id}_FunctionTableEnd, g_ps2Overlay_{overlay_id}_FunctionTableSlotCount;\n'
                                f'extern PS2Runtime::RecompiledFunction g_ps2Overlay_{overlay_id}_FunctionTable[];', 1)
        text = pattern.sub(lambda m: renames[m.group(1)], text)
        path.write_text(text)
        if path.name in ('ps2_recompiled_functions.h', 'ps2_recompiled_stubs.h'):
            path.rename(path.with_name(path.name.replace('ps2_', f'{overlay_id}_', 1)))
    for path in generated.glob('*.cpp'):
        text = path.read_text()
        for header in ('ps2_recompiled_functions.h', 'ps2_recompiled_stubs.h'):
            text = text.replace(f'<{header}>', f'<{header.replace("ps2_", f"{overlay_id}_", 1)}>')
        path.write_text(text)
    for path in sorted(generated.glob('*.cpp')):
        if path.name != 'register_functions.cpp' and not path.name.startswith(f'{overlay_id}_'):
            path.rename(path.with_name(f'{overlay_id}_{path.name}'))
    (generated / 'register_functions.cpp').rename(generated / f'{overlay_id}_register_functions.cpp')
    return len(names)


def manifest_source(overlays):
    """C++ that registers every accepted overlay table with the runtime overlay dispatcher."""
    lines = ['// Generated by overlays.py: overlay tables and content signatures.', '#include "ps2_runtime.h"',
             '#include "overlay_dispatch.hpp"', '']
    for o in overlays:
        oid = o['id']
        lines += [f'extern const uint32_t g_ps2Overlay_{oid}_FunctionTableBase, g_ps2Overlay_{oid}_FunctionTableEnd, '
                  f'g_ps2Overlay_{oid}_FunctionTableSlotCount;',
                  f'extern PS2Runtime::RecompiledFunction g_ps2Overlay_{oid}_FunctionTable[];',
                  f'static const ps2port::OverlaySignatureWord {oid}_signature[] = {{'
                  + ', '.join(f'{{0x{a:x}u, 0x{w:08x}u}}' for a, w in o['signature']) + '};']
    lines += ['', 'namespace {', 'struct OverlayRegistration {', '    OverlayRegistration() {']
    for o in overlays:
        oid = o['id']
        lines.append(f'        ps2port::overlayRegistry().add({{"{oid}", g_ps2Overlay_{oid}_FunctionTableBase, '
                     f'g_ps2Overlay_{oid}_FunctionTableEnd, g_ps2Overlay_{oid}_FunctionTable, '
                     f'g_ps2Overlay_{oid}_FunctionTableSlotCount, {oid}_signature, {len(o["signature"])}u}});')
    lines += ['    }', '} registration;', '}', '']
    return '\n'.join(lines)


def region_bytes(private, region):
    image = Path(private) / 'cd-image/disc.iso'
    if region['source'] == 'image':
        with image.open('rb') as stream:
            stream.seek(region['offset'])
            return stream.read(region['bytes'])
    with (Path(private) / 'disc-root' / region['file']).open('rb') as stream:
        stream.seek(region['offset'])
        return stream.read(region['bytes'])


def _configure(config, elf, output, hints):
    from feedback import config_with_hints
    text = config.read_text()
    for key, value in {'input': json.dumps(str(elf)), 'output': json.dumps(str(output)),
                       'single_file_output': 'false', 'low_memory_mode': 'true', 'output_worker_threads': '1'}.items():
        if re.search(rf'^\s*{key}\s*=', text, re.M):
            text = re.sub(rf'^\s*{key}\s*=.*$', lambda m: f'{key} = {value}', text, flags=re.M)
        else:
            text = text.replace('[general]', f'[general]\n{key} = {value}', 1)
    config.write_text(config_with_hints(text, hints))


def translate_overlays(profile, private):
    """Write overlays/<id>/generated for every trusted census region and analysis/overlay-translation.json."""
    import pipeline as lab
    from pointer_entries import scan
    private = Path(private).resolve()
    root = private / 'overlays'
    if root.exists():
        shutil.rmtree(root)
    if profile.get('overlay_translation', True) is False:
        report = dict(schema=1, title_id=profile['id'], skipped='profile overlay_translation is false', overlays=[],
                      accepted_overlays=0, functions=0, runtime_verified=False)
        lab.save(private / 'analysis/overlay-translation.json', report)
        print('Overlay translation skipped by profile')
        return report
    census_path = private / 'analysis/code-census.json'
    if not census_path.is_file():
        raise ValueError('Run code-census first')
    accepted, rejected = candidates(json.loads(census_path.read_text()))
    root.mkdir(parents=True)
    programs, records = {}, []
    for oid, region in accepted:
        base = int(region['estimated_load_address'], 16)
        data = region_bytes(private, region)
        spans = extend_spans(data, base, code_spans(data))
        if not spans:
            records.append(dict(id=oid, file=region['file'], offset=region['offset'], bytes=region['bytes'],
                                load_address=region['estimated_load_address'], accepted=False,
                                blockers=['no code span'], counts={}, functions=0, signature=[]))
            continue
        programs[oid] = (base, data, spans)
        work = root / oid
        work.mkdir()
        elf = work / 'program.elf'
        raw = wrap_elf(data, base, spans)
        elf.write_bytes(raw)
        config = work / 'recomp.toml'
        lab.run([lab.BUILD / 'ps2xAnalyzer/ps2_analyzer', elf, config], work / 'analyze.log', offline=True, cwd=work)
        hints = scan(raw)
        _configure(config, elf, work / 'generated', [f'ptr_{at:08X}@0x{at:08X}' for at, _ in hints])
        try:
            lab.run([lab.BUILD / 'ps2xRecomp/ps2_recomp', config], work / 'translate.log', offline=True, cwd=work)
        except Exception as error:  # recorded per overlay; one bad program must not hide the others
            (work / 'translate.log').open('a').write(f'\n{error}\n')
        acceptance = lab.translation_acceptance((work / 'translate.log').read_text(errors='replace'))
        functions = qualify(work / 'generated', oid) if acceptance['accepted'] else 0
        elf.unlink()  # disc bytes stay out of the output tree; the region offset reproduces them
        records.append(dict(id=oid, file=region['file'], offset=region['offset'], bytes=region['bytes'],
                            joined_regions=region.get('joined_regions', 1),
                            load_address=region['estimated_load_address'],
                            code_spans=[[f'0x{base + s:08x}', f'0x{base + e:08x}'] for s, e in spans],
                            code_bytes=sum(e - s for s, e in spans), entry_hints=len(hints),
                            accepted=acceptance['accepted'], blockers=acceptance['blockers'],
                            counts=acceptance['counts'], functions=functions))
    boot = boot_image(lab.boot_executable(private / 'disc-root', profile))
    sigs = signatures(programs, boot)
    for record in records:
        record['signature'] = sigs.get(record['id'], [])
    usable = [r for r in records if r['accepted'] and len(r['signature']) == SIGNATURE_WORDS]
    (root / 'overlay_manifest.cpp').write_text(manifest_source(usable))
    report = dict(schema=1, title_id=profile['id'], local_only=True, translation_input='code-census',
                  min_vote_ratio=MIN_VOTE_RATIO, signature_words=SIGNATURE_WORDS,
                  overlays=[{k: v for k, v in r.items() if k != 'signature'} | {'signature_words': len(r['signature']),
                            'registered': r in usable} for r in records],
                  rejected=rejected, accepted_overlays=len(usable), translated_overlays=len(records),
                  functions=sum(r['functions'] for r in usable),
                  decode_failures=sum(r['counts'].get('decode_failures', 0) for r in records),
                  errors=sum(r['counts'].get('errors', 0) for r in records),
                  native_build_verified=False, runtime_verified=False)
    lab.save(private / 'analysis/overlay-translation.json', report)
    print(f"Overlay translation: {len(usable)}/{len(records)} overlays registered, {report['functions']} functions, "
          f"{report['decode_failures']} decode failures, {report['errors']} errors, "
          f"{len(rejected)} regions without a trusted load address")
    return report


def check_overlays(private):
    """Syntax-check the registration source and a sample of each registered overlay's generated C++."""
    import pipeline as lab
    import subprocess
    private = Path(private).resolve()
    report = json.loads((private / 'analysis/overlay-translation.json').read_text())
    root, records = private / 'overlays', []
    registered = [o['id'] for o in report.get('overlays', []) if o.get('registered')]
    sample = [(root / 'overlay_manifest.cpp', [Path(__file__).parent / 'runtime/overlays'])] if registered else []
    for i, oid in enumerate(registered):
        generated = root / oid / 'generated'
        files = sorted(p for p in generated.glob('*.cpp') if not p.name.startswith('._'))
        bodies = [p for p in files if not p.name.endswith('_register_functions.cpp')]
        picks = {max(bodies, key=lambda p: p.stat().st_size)} if bodies else set()
        if i == 0:
            picks.add(generated / f'{oid}_register_functions.cpp')  # renamed table symbols
        sample += [(p, [generated]) for p in sorted(picks)]
    logs = private / 'logs/overlay-syntax'
    logs.mkdir(parents=True, exist_ok=True)
    for path, includes in sample:
        log = logs / (path.name + '.log')
        with log.open('w') as f:
            result = subprocess.run([*lab.syntax_check_command(*includes), str(path)], stdout=f, stderr=subprocess.STDOUT)
        records.append(dict(file=str(path.relative_to(private)), returncode=result.returncode))
        print(f'{"PASS" if result.returncode == 0 else "FAIL"} {path.name}', flush=True)
    report['syntax_sample'] = dict(files=records, success=all(r['returncode'] == 0 for r in records))
    lab.save(private / 'analysis/overlay-translation.json', report)
    if not report['syntax_sample']['success']:
        raise RuntimeError('Generated overlay source failed native syntax checks; inspect logs/overlay-syntax')
    return report


def boot_image(path):
    """(base, bytes) of the boot executable's lowest loadable span, for signature comparison."""
    raw = Path(path).read_bytes()
    phoff, phentsize, phnum = struct.unpack_from('<I', raw, 28)[0], *struct.unpack_from('<2H', raw, 42)
    spans = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from('<5I', raw, phoff + i * phentsize)
        if p_type == PT_LOAD and p_filesz:
            spans.append((p_vaddr, raw[p_offset:p_offset + p_filesz]))
    if not spans:
        return None
    spans.sort()
    base, data = spans[0][0], bytearray(spans[0][1])
    for vaddr, chunk in spans[1:]:
        if vaddr >= base + len(data):
            data += bytes(vaddr - base - len(data))
        data[vaddr - base:vaddr - base + len(chunk)] = chunk
    return base, bytes(data)
