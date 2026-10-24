"""Verified sector image of a privately owned disc, for titles that read ISO9660 by LBN.

The file tree from `import` is enough for titles that open files by name. Some titles read the
filesystem by logical block number (sceCdRead from the primary volume descriptor), which only a
sector image can answer. The image is copied from an explicit local source (a raw disc device or
an existing .iso), trimmed to the volume size recorded in the primary volume descriptor (raw
copies often carry device padding), and accepted only when every file extent inside it hashes to
the disc manifest written by `import`.
"""
from pathlib import Path
import hashlib
import json
import struct
import time

SECTOR = 2048
PVD_SECTOR = 16
IMAGE = 'cd-image/disc.iso'
STAGE = 'analysis/cd-image-stage.json'
MAX_DEPTH = 32


def _read(stream, lba, size):
    stream.seek(lba * SECTOR)
    return stream.read(size)


def volume_sectors(stream):
    pvd = _read(stream, PVD_SECTOR, SECTOR)
    if len(pvd) < SECTOR or pvd[0] != 1 or pvd[1:6] != b'CD001':
        raise ValueError('No ISO9660 primary volume descriptor at sector 16')
    sectors, = struct.unpack_from('<I', pvd, 80)
    block, = struct.unpack_from('<H', pvd, 128)
    if block != SECTOR or sectors <= PVD_SECTOR:
        raise ValueError('Unsupported ISO9660 logical block size or volume size')
    return sectors, pvd


def extents(stream):
    """Map of 'DIR/FILE' -> (extent LBN, byte size) for every file in the ISO9660 tree."""
    sectors, pvd = volume_sectors(stream)
    files, seen = {}, set()

    def walk(lba, size, prefix, depth):
        if depth > MAX_DEPTH or lba in seen or lba + (size + SECTOR - 1) // SECTOR > sectors:
            raise ValueError('Invalid ISO9660 directory structure')
        seen.add(lba)
        data = _read(stream, lba, size)
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:  # records never straddle sectors
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            record = data[pos:pos + length]
            pos += length
            if length < 34 or 33 + record[32] > length:
                raise ValueError('Invalid ISO9660 directory record')
            extent, = struct.unpack_from('<I', record, 2)
            data_size, = struct.unpack_from('<I', record, 10)
            name = record[33:33 + record[32]]
            if name in (b'\x00', b'\x01'):
                continue
            name = name.decode('ascii', 'replace').split(';')[0]
            if not name or '/' in name or name in ('.', '..'):
                raise ValueError('Invalid ISO9660 file name')
            if record[25] & 2:
                walk(extent, data_size, prefix + name + '/', depth + 1)
            else:
                if extent + (data_size + SECTOR - 1) // SECTOR > sectors:
                    raise ValueError('ISO9660 file extent lies outside the volume')
                files[prefix + name] = (extent, data_size)

    root = pvd[156:190]
    walk(struct.unpack_from('<I', root, 2)[0], struct.unpack_from('<I', root, 10)[0], '', 0)
    return sectors, files


def verify_image(image, manifest):
    """Hash every manifest file's extent inside the image. Returns a report; never raises on mismatch."""
    records = manifest.get('files') or []
    with Path(image).open('rb') as stream:
        sectors, files = extents(stream)
        missing, mismatched = [], []
        for record in records:
            key = record['path'].replace('\\', '/')
            match = files.get(key) or files.get(key.upper())
            if match is None:
                missing.append(record['path'])
                continue
            extent, size = match
            digest = hashlib.sha256()
            stream.seek(extent * SECTOR)
            left = size
            while left:
                chunk = stream.read(min(left, 1 << 22))
                if not chunk:
                    break
                digest.update(chunk)
                left -= len(chunk)
            if left or size != record['bytes'] or digest.hexdigest() != record['sha256']:
                mismatched.append(record['path'])
    size = Path(image).stat().st_size
    return {'volume_sectors': sectors, 'image_bytes': size, 'iso_files': len(files),
            'manifest_files': len(records), 'missing': missing[:50], 'mismatched': mismatched[:50],
            'verified': bool(records) and not missing and not mismatched and size == sectors * SECTOR}


def import_image(source, private):
    """Copy, trim and verify a sector image into private storage; record the stage."""
    private = Path(private)
    source = Path(source)
    manifest_path = private / 'disc-manifest.json'
    if not manifest_path.is_file():
        raise ValueError('Run import first: the image is verified against the disc manifest')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('complete') is False or not manifest.get('files'):
        raise ValueError('Image verification needs a complete disc manifest')
    (private / STAGE).unlink(missing_ok=True)
    images = (private / 'cd-image').resolve()
    if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(images):
        # Already in private storage and exactly volume-sized: verify in place, no second copy.
        with source.open('rb') as reader:
            sectors, _ = volume_sectors(reader)
        if source.stat().st_size == sectors * SECTOR:
            return _accept(private, source.resolve(), source, verify_image(source, manifest), trimmed=False)
    target = private / IMAGE
    if source.resolve() == target.resolve():
        raise ValueError('Private image is not volume-sized; import it again from the disc')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.partial')
    with source.open('rb') as reader:
        sectors, _ = volume_sectors(reader)
        reader.seek(0)
        left = sectors * SECTOR
        with temporary.open('wb') as writer:
            while left:
                block = reader.read(min(left, 1 << 22))
                if not block:
                    break
                writer.write(block)
                left -= len(block)
    if left:
        temporary.unlink()
        raise ValueError('Source image is shorter than its ISO9660 volume size')
    report = verify_image(temporary, manifest)
    if not report['verified']:
        temporary.unlink()
        raise ValueError(f'Sector image does not match the disc manifest: '
                         f'{len(report["missing"])} missing, {len(report["mismatched"])} mismatched')
    temporary.replace(target)
    return _accept(private, target, source, report,
                   trimmed=source.is_file() and source.stat().st_size != report['image_bytes'])


def _accept(private, image, source, report, trimmed):
    if not report['verified']:
        raise ValueError(f'Sector image does not match the disc manifest: '
                         f'{len(report["missing"])} missing, {len(report["mismatched"])} mismatched')
    stage = dict(report, image=str(Path(image).resolve().relative_to(Path(private).resolve())),
                 source=str(source), trimmed_from_source=trimmed, local_only=True, verified_at=time.time())
    Path(private).joinpath(STAGE).parent.mkdir(parents=True, exist_ok=True)
    Path(private).joinpath(STAGE).write_text(json.dumps(stage, indent=2) + '\n')
    print(f'Verified sector image: {report["volume_sectors"]} sectors, {report["manifest_files"]} files')
    return stage


def verified_image(private):
    """Absolute path of the accepted image, or None. Cheap check: stage record and exact size."""
    private = Path(private)
    stage_path = private / STAGE
    if not stage_path.is_file():
        return None
    stage = json.loads(stage_path.read_text())
    image = private / stage.get('image', IMAGE)
    if (not stage.get('verified') or image.is_symlink() or not image.is_file()
            or not image.resolve().is_relative_to(private.resolve())
            or image.stat().st_size != stage.get('image_bytes')):
        return None
    return image.resolve()
