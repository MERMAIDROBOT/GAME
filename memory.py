"""Local reusable EE memory ownership gate. No game addresses in the algorithm.

Checks explicit reservations against ELF load memory (including BSS) and supplied
guest/host ranges. Passing only covers declared ranges, not all PS2 programs.
"""
import hashlib
import json
import re
import struct
from pathlib import Path

RAM_BYTES = 32 * 1024 * 1024


def region(name, start, size):
    if size <= 0 or start < 0 or start >= RAM_BYTES or size > RAM_BYTES-start:
        raise ValueError('Invalid physical EE region: '+name)
    return dict(name=name, start=start, end=start+size)


def elf_regions(raw):
    if len(raw) < 52 or raw[:6] != b'\x7fELF\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    if struct.unpack_from('<H', raw, 18)[0] != 8:
        raise ValueError('Expected MIPS ELF')
    offset = struct.unpack_from('<I', raw, 28)[0]
    stride, count = struct.unpack_from('<HH', raw, 42)
    if stride < 32 or count == 0 or offset+stride*count > len(raw):
        raise ValueError('Invalid program header table')
    result = []
    for i in range(count):
        kind, fileoff, virtual, physical, filesz, memsz, flags, align = struct.unpack_from('<8I', raw, offset+i*stride)
        if kind != 1 or memsz == 0:
            continue
        if filesz > memsz or fileoff+filesz > len(raw):
            raise ValueError('Invalid ELF segment bounds')
        # Runtime loads by virtual address. Resolve only known direct-mapped
        # physical/KSEG0/KSEG1 addresses; do not silently mask arbitrary values.
        if virtual < RAM_BYTES:
            address = virtual
        elif 0x80000000 <= virtual < 0xc0000000:
            address = virtual & 0x1fffffff
        else:
            raise ValueError('ELF needs unsupported virtual mapping')
        result.append(region('ELF PT_LOAD '+str(i), address, memsz))
    if not result:
        raise ValueError('No nonempty ELF load segments')
    return result


def pools(header):
    text = header.read_text()
    result = []
    for name in ['RpcPacket', 'RpcServer', 'Tls', 'BootMode']:
        def value(suffix):
            match = re.search(r'\bk'+name+'Pool'+suffix+r'\s*=\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*;', text)
            if not match:
                raise ValueError('Unrecognized pool constant '+name+suffix)
            return int(match[1], 0)
        result.append(region(name+' runtime pool', value('Base'), value('Bytes')))
    return result


def collisions(reservations, occupied):
    failures = []
    for i, reservation in enumerate(reservations):
        for other in occupied+reservations[:i]:
            start = max(reservation['start'], other['start'])
            end = min(reservation['end'], other['end'])
            if start < end:
                failures.append(dict(reservation=reservation['name'],
                    conflicts_with=other['name'], start=start, end=end,
                    overlap_bytes=end-start))
    return failures


def run_audit(profile, private, header, layout, name):
    """Run an explicit local memory gate, preserving rejected audit reports."""
    from pipeline import boot_executable, digest, save
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('Audit name must contain lowercase letters, digits, hyphens or underscores')
    private = Path(private).resolve()
    layout = (private / layout).resolve()
    if not layout.is_relative_to(private) or not layout.is_file():
        raise ValueError('Memory layout must be a file inside private game storage')
    header = Path(header).resolve()
    elf = boot_executable(private/'disc-root', profile)
    identity = digest(elf)
    if profile.get('expected_sha256') and identity != profile['expected_sha256']:
        raise ValueError('Executable differs from profile fingerprint')
    target = private/'analysis/memory-audits'/name
    if not target.resolve().is_relative_to(private):
        raise ValueError('Audit output escapes private game storage')
    target.mkdir(parents=True, exist_ok=False)
    report = dict(local_only=True, status='rejected', accepted=False,
        executable_sha256=identity,
        scope='Declared physical EE range disjointness only. Not complete dynamic '
              'allocation coverage, hardware compatibility or gameplay proof.')
    try:
        data = layout.read_bytes()
        report['layout_sha256'] = hashlib.sha256(data).hexdigest()
        report['runtime_header_sha256'] = digest(header)
        declared = json.loads(data)
        occupied = elf_regions(elf.read_bytes())
        occupied += [region(x['name'], int(x['start'], 0), int(x['size'], 0))
                     for x in declared['occupied']]
        reservations = pools(header)
        failures = collisions(reservations, occupied)
        report.update(reservations=reservations, occupied=occupied,
                      conflicts=failures, accepted=not failures,
                      status='disjoint' if not failures else 'collision')
    except Exception as error:
        report['error'] = str(error)
        save(target/'report.json', report)
        raise
    save(target/'report.json', report)
    return report

