"""Qualify direct overlay-to-resident calls before native linking.

Only original instruction bytes that the accepted overlay translator decoded can
seed resident entries. Never infer functions from arbitrary address-like data or
bind an overlapping resident address while the overlay owns it.
"""
from pathlib import Path
import hashlib
import json
import re
import struct


def registered(directory):
    table = Path(directory) / 'register_functions.cpp'
    if not table.is_file():
        raise ValueError('Resident registration table missing')
    return {int(x, 16) for x in re.findall(r'\[[0-9]+u?\]\s*=\s*\w+\s*;\s*//\s*0x([0-9a-fA-F]+)', table.read_text())}


def direct_targets(raw, base, spans, texts, resident_spans, covered):
    """Pure qualification of original decoded call sites; returns missing entries."""
    targets = set()
    for text in texts:
        for pc, word in re.findall(r'// 0x([0-9a-fA-F]+): 0x([0-9a-fA-F]{1,8})\b', text):
            pc, word = int(pc, 16), int(word, 16)
            # J/JAL are unambiguous absolute direct transfers. Conditional calls
            # and indirect targets need their own evidence, not guessed values.
            if word >> 26 not in (2, 3):
                continue
            target = ((pc + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
            if base <= target < base + len(raw) or target in covered:
                continue
            if not any(a <= target and target + 4 <= b for a, b, _ in resident_spans):
                continue
            offset = pc - base
            if pc & 3 or not 0 <= offset <= len(raw) - 4 or not any(a <= pc and pc + 4 <= b for a, b in spans):
                raise ValueError('Overlay call site is outside accepted executable bytes')
            if struct.unpack_from('<I', raw, offset)[0] != word:
                raise ValueError('Decoded overlay call differs from original disc bytes')
            targets.add(target)
    return targets


def discover(profile, private):
    import pipeline as lab
    import overlays
    from feedback import executable_spans
    private = Path(private).resolve()
    path = private / 'analysis/overlay-translation.json'
    if profile.get('overlay_build') is False or not path.is_file():
        return dict(entries=[], evidence=[])
    report = json.loads(path.read_text())
    if not report.get('accepted_overlays'):
        return dict(entries=[], evidence=[])
    raw = lab.boot_executable(private / 'disc-root', profile).read_bytes()
    if profile.get('expected_sha256') and hashlib.sha256(raw).hexdigest() != profile['expected_sha256']:
        raise ValueError('Resident executable revision differs from profile')
    resident = executable_spans(raw)
    covered = registered(private / 'generated')
    census = json.loads((private / 'analysis/code-census.json').read_text())
    regions = dict(overlays.candidates(census)[0])
    targets, all_targets, evidence = set(), set(), []
    for record in report['overlays']:
        if not record.get('registered'):
            continue
        oid = record['id']
        if not re.fullmatch(r'o[0-9]+', oid) or oid not in regions:
            raise ValueError('Unqualified overlay identity')
        region = regions[oid]
        for key in ('file', 'offset', 'bytes'):
            if record[key] != region[key]:
                raise ValueError('Overlay differs from accepted disc census')
        base = int(record['load_address'], 16)
        if record.get('source_layout') is not None:
            image = overlays.program_image(private, region)
            if image['layout'] != record['source_layout'] or image['base'] != base:
                raise ValueError('Overlay section layout differs from original container headers')
            data = image['data']
        else:
            if base != int(region['estimated_load_address'], 16):
                raise ValueError('Overlay load address differs from accepted census')
            data = overlays.region_bytes(private, region)
            if len(data) != record['bytes']:
                raise ValueError('Truncated original overlay bytes')
        spans = [(int(a, 16), int(b, 16)) for a, b in record['code_spans']]
        sources = lab.generated_sources(private / 'overlays' / oid / 'generated')
        found = direct_targets(data, base, spans, (p.read_text() for p in sources), resident, set())
        all_targets.update(found)
        targets.update(found - covered)
        if found:
            evidence.append(dict(overlay=oid, original_sha256=hashlib.sha256(data).hexdigest(),
                                 targets=[f'{at:08x}' for at in sorted(found)]))
    return dict(entries=[f'{at:08x}' for at in sorted(targets)],
                all_entries=[f'{at:08x}' for at in sorted(all_targets)], evidence=evidence,
                executable_sha256=hashlib.sha256(raw).hexdigest(), gameplay_verified=False)


def configuration(profile, private, config):
    from feedback import config_with_hints
    from pipeline import save
    report = discover(profile, private)
    entries = report.get('all_entries', report['entries'])
    if not entries:
        return config, report
    result = Path(private) / 'analysis/overlay-resident-recomp.toml'
    result.write_text(config_with_hints(Path(config).read_text(),
                      ['overlay_resident_' + at + '@0x' + at for at in entries]))
    report['configuration_sha256'] = hashlib.sha256(result.read_bytes()).hexdigest()
    save(Path(private) / 'analysis/overlay-resident-entries.json', report)
    return result, report


def ensure(profile, private):
    """Refresh real native guest code; no runtime address aliases or fake returns."""
    import pipeline as lab
    report = discover(profile, private)
    from sdk_policy import audit_native_image_bindings
    config = Path(private) / 'analysis/recomp.toml'
    bindings = audit_native_image_bindings(config, Path(private) / 'generated') if config.is_file() else dict(passed=True)
    from indirect_entries import audit as audit_indirect_entries
    indirect = audit_indirect_entries(Path(private) / 'generated')
    if not report['entries'] and bindings['passed'] and indirect['passed']:
        return report
    # Retain same-byte timestamps to spare unaffected resident compilation units.
    old = {p.name: (p.read_bytes(), p.stat()) for p in (Path(private) / 'generated').iterdir()
           if p.is_file() and p.suffix in ('.cpp', '.h')}
    from tool_state import ensure as ensure_tools
    ensure_tools(lab)
    lab.translate(profile, private)
    for source in (Path(private) / 'generated').iterdir():
        previous = old.get(source.name)
        if previous and source.read_bytes() == previous[0]:
            import os
            stat = previous[1]
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    remaining = discover(profile, private)
    bindings = audit_native_image_bindings(config, Path(private) / 'generated') if config.is_file() else dict(passed=True)
    lab.save(Path(private) / 'analysis/native-image-bindings.json', bindings)
    indirect = audit_indirect_entries(Path(private) / 'generated')
    lab.save(Path(private) / 'analysis/native-indirect-entries.json', indirect)
    if not indirect['passed']:
        raise ValueError('Native register-jump entry coverage still incomplete after translation')
    if not bindings['passed']:
        raise ValueError('Configured native libgraph bindings still use guest bodies after translation')
    if remaining['entries']:
        raise ValueError('Resident entries still missing after real translation: ' + ','.join(remaining['entries']))
    lab.save(Path(private) / 'analysis/overlay-resident-closure.json',
             dict(requested=report, remaining=remaining, source_qualified=True, gameplay_verified=False))
    return remaining
