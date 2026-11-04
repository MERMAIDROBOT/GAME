"""Disc code census: MIPS code found outside the boot executable (report only).

Many titles load code from the disc at run time (level programs, overlays, IOP modules), so the
boot ELF can be a small part of a game's code. This stage scans the accepted sector image (or,
without one, every file in the disc copy) in fixed windows, keeps windows dense in `jr ra` returns
and `addiu sp,sp,-N` prologues, merges them into regions and estimates each region's load address
from its own `jal` targets: the base that puts the most call targets on the region's prologues.

Regions are labelled by the ISO9660 file that holds them or as
`unlisted_sectors` when no file covers them (hidden tables of contents). A file counts as IOP code
(not EE) when it is an IOP relocatable ELF (e_type 0xFF80), an IOP reset image (`RESET` romdir) or
named `.irx`. The boot executable is
excluded. Results are candidates for review and for later translation work, never translation input
and never evidence of correctness.
"""
from collections import Counter
from pathlib import Path
import struct

WINDOW = 64 * 1024
MIN_RETURNS = 16       # aligned `jr ra` per window
MIN_PROLOGUES = 8      # aligned `addiu sp,sp,-N` per window
MAX_TARGETS = 800      # jal targets sampled per region for base voting
MAX_STARTS = 6000      # prologue offsets sampled per region
ADDRESS_LIMIT = 0x2000000  # EE main RAM
JR_RA = b'\x08\x00\xe0\x03'


def _aligned_count(buf, pattern, position):
    count, at = 0, buf.find(pattern)
    while at != -1:
        if (position + at) % 4 == 0:
            count += 1
        at = buf.find(pattern, at + 1)
    return count


def _prologue_count(buf, position):
    # little-endian `addiu sp,sp,imm` with a negative imm: [lo, 0x80..0xff, 0xbd, 0x27]
    count, at = 0, buf.find(b'\xbd\x27')
    while at != -1:
        if at >= 2 and (position + at - 2) % 4 == 0 and buf[at - 1] & 0x80:
            count += 1
        at = buf.find(b'\xbd\x27', at + 1)
    return count


def code_windows(stream, size):
    """Offsets of WINDOW-sized blocks of `stream` dense in returns and prologues."""
    found = []
    for offset in range(0, size, WINDOW):
        stream.seek(offset)
        buf = stream.read(min(WINDOW, size - offset))
        if _aligned_count(buf, JR_RA, offset) >= MIN_RETURNS and _prologue_count(buf, offset) >= MIN_PROLOGUES:
            found.append(offset)
    return found


def merge(windows, size):
    regions = []
    for offset in windows:
        end = min(offset + WINDOW, size)
        if regions and regions[-1][1] == offset:
            regions[-1][1] = end
        else:
            regions.append([offset, end])
    return [tuple(r) for r in regions]


def infer_base(buf):
    """(load address of buf[0], votes, sampled targets) by matching jal targets to prologue offsets."""
    words = struct.unpack_from(f'<{len(buf) // 4}I', buf)
    starts = [4 * i for i, w in enumerate(words) if w & 0xFFFF8000 == 0x27BD8000][:MAX_STARTS]
    targets = sorted({(w & 0x03FFFFFF) << 2 for w in words if w >> 26 == 3})[:MAX_TARGETS]
    votes = Counter(t - s for t in targets for s in starts if 0 <= t - s < ADDRESS_LIMIT)
    if not votes:
        return None, 0, len(targets)
    base, count = votes.most_common(1)[0]
    return base, count, len(targets)


def _is_iop(name, header):
    """IOP code by content (relocatable IRX ELF, IOPRP reset image) or by the .irx name."""
    if header[:4] == b'\x7fELF' and len(header) >= 18 and struct.unpack_from('<H', header, 16)[0] in (0xFF80, 0xFF81):
        return True
    if header[:6] == b'RESET\x00':
        return True
    return name.lower().split(';')[0].endswith('.irx')


def _label(start, end, files):
    """The file holding most of [start, end): window-sized regions overhang file edges."""
    best, most = None, 0
    for name, (first, last) in files.items():
        overlap = min(end, last) - max(start, first)
        if overlap > most:
            best, most = name, overlap
    return best if most * 2 >= end - start else None  # a file labels only a region it holds at least half of


def census(private, profile):
    """Write analysis/code-census.json for the accepted image or the disc copy."""
    import pipeline as lab
    private = Path(private).resolve()
    boot = lab.boot_executable(private / 'disc-root', profile)
    boot_bytes = boot.read_bytes()
    image = private / 'cd-image/disc.iso'
    sources = []
    if image.is_file():
        from cdimage import SECTOR, extents
        with image.open('rb') as stream:
            _, table = extents(stream)
            files = {name: (lba * SECTOR, lba * SECTOR + size) for name, (lba, size) in table.items()}
        sources.append(('image', image, files))
    else:
        root = private / 'disc-root'
        for path in sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()):
            if path.resolve() != boot.resolve():
                sources.append(('file', path, {str(path.relative_to(root)): (0, path.stat().st_size)}))
    regions = []
    for kind, path, files in sources:
        size = path.stat().st_size
        with path.open('rb') as stream:
            for start, end in merge(code_windows(stream, size), size):
                name = _label(start, end, files)
                if kind == 'image' and name is not None and Path(name.split(';')[0]).name.upper() == boot.name.upper():
                    continue  # the boot executable is translated by the main pipeline
                stream.seek(start)
                buf = stream.read(end - start)
                if kind == 'image' and boot_bytes[:4096] in buf:
                    continue
                base, votes, sampled = infer_base(buf)
                label = name if kind == 'file' else (name or 'unlisted_sectors')
                header = b''
                if name is not None:
                    stream.seek(files[name][0])
                    header = stream.read(64)
                is_iop = _is_iop(label, header)
                regions.append(dict(source=kind, file=label, offset=start, bytes=end - start,
                                    kind='iop_module' if is_iop else 'ee_code_candidate',
                                    estimated_load_address=None if base is None else f'0x{base:08x}',
                                    base_votes=votes, sampled_call_targets=sampled))
    ee = [r for r in regions if r['kind'] == 'ee_code_candidate']
    report = dict(schema=1, title_id=profile['id'], local_only=True, translation_input=False,
                  window_bytes=WINDOW, thresholds=dict(returns=MIN_RETURNS, prologues=MIN_PROLOGUES),
                  boot_executable_bytes=len(boot_bytes), scanned='sector image' if image.is_file() else 'disc files',
                  regions=regions, ee_candidate_regions=len(ee), ee_candidate_bytes=sum(r['bytes'] for r in ee),
                  iop_module_bytes=sum(r['bytes'] for r in regions if r['kind'] == 'iop_module'),
                  unlisted_sector_bytes=sum(r['bytes'] for r in ee if r['file'] == 'unlisted_sectors'))
    lab.save(private / 'analysis/code-census.json', report)
    print(f"Code outside the boot executable: {report['ee_candidate_regions']} EE candidate regions, "
          f"{report['ee_candidate_bytes']} bytes ({report['unlisted_sector_bytes']} in sectors no file lists); "
          f"{report['iop_module_bytes']} bytes in IOP modules")
    return report
