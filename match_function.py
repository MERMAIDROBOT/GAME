"""Compile one function with a pinned original-era PS2 compiler and compare it with the same function in the disc's
executable.

Compilers are 32-bit Linux programs (the decompme/compilers EE GCC builds). They run in an isolated container
(no network, compiler mounted read-only) through the local Docker context. The object's own assembler output is
compared word by word; fields its relocations fill (call targets, %hi/%lo, %gp_rel, pointers) are masked, since
they are set at link time. Generic: no title data. Sources, objects and reports stay in private storage.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import struct
import subprocess

import binary_rebuild as rebuild
import isa_r5900 as isa

# Container image for 32-bit Linux tools, pinned by digest.
IMAGE = 'i386/debian:bookworm-slim@sha256:a374a55607846b2be38f8a99c56ce70928b0624d9da3e8ac48c9e39970012792'
# Relocation type -> bits of the word the linker fills.
RELOC_MASKS = {2: 0xffffffff, 4: 0x03ffffff, 5: 0xffff, 6: 0xffff, 7: 0xffff}


def compiler_root():
    import pipeline as lab
    return Path(lab.os.environ.get('PS2_PORT_LAB_TOOLCHAIN', lab.ROOT.parent / 'toolchain')) / 'ps2-compilers'


def compilers(root=None):
    """{id: directory} for extracted compilers whose downloaded archive matches SHA256SUMS."""
    root = Path(root or compiler_root())
    sums = {}
    if (root / 'SHA256SUMS').is_file():
        for line in (root / 'SHA256SUMS').read_text().splitlines():
            digest, path = line.split(None, 1)
            sums[Path(path.strip()).name] = digest
    result = {}
    for archive in sorted((root / 'downloads').glob('*')):
        ident = re.sub(r'\.tar\.(xz|gz)$', '', archive.name)
        directory = root / 'extracted' / ident
        if ((directory / 'bin/ee-gcc').is_file() or (directory / 'bin/ee-gcc.exe').is_file()) and sums.get(archive.name) == hashlib.sha256(archive.read_bytes()).hexdigest():
            result[ident] = directory
    return result


# What the Windows drivers' specs add for the EE (predefines, cpp_spec, size types, 64-bit long).
WINDOWS_PREDEFINES = ('-D__GNUC__=2', '-D__GNUC_MINOR__=95', '-Dmips', '-DMIPSEL', '-DR5900', '-D_mips', '-D_MIPSEL',
                      '-D_R5900', '-D__mips__', '-D__MIPSEL__', '-D__R5900__', '-D__mips=3', '-D__mips64',
                      '-D__mips_eabi', '-D__mips_single_float', '-D__SIZE_TYPE__=unsigned',
                      '-D__PTRDIFF_TYPE__=int', '-D__LONG_MAX__=9223372036854775807L', '-D__ELF__')
WIBO = 'tools/wibo-1.2.0/wibo-i686'
WIBO_SHA256 = '2575d3b0a2f408b2c2b0850db56f1af5d005a138394a6774eba77b6708ecc304'


def wibo_path():
    path = compiler_root() / WIBO
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != WIBO_SHA256:
        raise ValueError('Windows compilers need the pinned wibo loader at ' + str(path))
    return path


def parse_object(raw):
    """Relocatable ELF -> (sections {name: bytes}, functions {name: (section, offset, size)},
    relocations {section: [(offset, type)]})."""
    elf = rebuild.parse_elf(raw)
    if elf['header'][1] != 1:
        raise ValueError('Expected a relocatable object')
    names, shdrs = elf['names'], elf['shdrs']
    sections = {names[i]: raw[s[4]:s[4] + s[5]] for i, s in enumerate(shdrs) if s[1] == 1}
    functions, relocations = {}, {}
    for i, s in enumerate(shdrs):
        if s[1] == 2:
            strings = shdrs[s[6]]
            for offset in range(s[4], s[4] + s[5], 16):
                name_off, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', raw, offset)
                if info & 15 == 2 and 0 < shndx < len(shdrs):
                    name = raw[strings[4] + name_off:raw.index(b'\0', strings[4] + name_off)].decode('latin-1')
                    functions[name] = (names[shndx], value, size)
        elif s[1] in (9, 4):                                  # SHT_REL / SHT_RELA
            entry = 8 if s[1] == 9 else 12
            target = names[s[7]]
            for offset in range(s[4], s[4] + s[5], entry):
                r_offset, r_info = struct.unpack_from('<II', raw, offset)
                relocations.setdefault(target, []).append((r_offset, r_info & 0xff))
    return sections, functions, relocations


def compare(original, compiled, relocations=()):
    """Word-by-word comparison with relocated fields masked. Returns (match, rows)."""
    masks = {}
    for offset, kind in relocations:
        masks[offset] = masks.get(offset, 0) | RELOC_MASKS.get(kind, 0xffffffff)
    rows, match = [], len(original) == len(compiled)
    for k in range(max(len(original), len(compiled)) // 4):
        a = struct.unpack_from('<I', original, 4 * k)[0] if 4 * k < len(original) else None
        b = struct.unpack_from('<I', compiled, 4 * k)[0] if 4 * k < len(compiled) else None
        mask = ~masks.get(4 * k, 0) & 0xffffffff
        same = a is not None and b is not None and (a & mask) == (b & mask)
        match &= same
        rows.append(dict(offset=4 * k, original=_text(a), compiled=_text(b), same=same, masked=mask != 0xffffffff))
    return match, rows


def _text(word):
    if word is None:
        return '-'
    row = isa.decode(word)
    if row is None:
        return f'.word {word:#010x}'
    spec, values = row
    return (isa.mnemonic_text(spec, values) + ' ' +
            ', '.join(rebuild._operand_text(spec, values, o, None) for o in spec.operands)).strip()


def compile_source(compiler, source, work, flags=('-O2', '-G0'), docker='docker'):
    """Compile source (C, or C++ by suffix) inside the container; return the object bytes."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for header in Path(source).parent.glob('*.h'):          # local headers beside the source
        shutil.copy2(header, work / header.name)
    shutil.copy2(source, work / Path(source).name)
    obj = Path(source).stem + '.o'
    compiler = Path(compiler).resolve()
    mounts = ['-v', f'{compiler}:/cc:ro', '-v', f'{work.resolve()}:/w']
    if (compiler / 'bin/ee-gcc.exe').is_file():
        # Windows builds run under wibo. Its process creation does not work in the container, so the driver's
        # stages run directly: preprocess (driver predefines), compile, assemble with the build's own assembler.
        loader = wibo_path()
        mounts += ['-v', f'{loader.parent}:/t:ro']
        version = next((compiler / 'lib/gcc-lib/ee').iterdir()).name
        lib = f'/cc/lib/gcc-lib/ee/{version}'
        stem = Path(source).stem
        tool = '/t/' + loader.name
        cc1 = 'cc1plus.exe' if Path(source).suffix in ('.cpp', '.cc', '.C', '.cxx') else 'cc1.exe'
        defines = ' '.join(WINDOWS_PREDEFINES)
        script = (f'{tool} {lib}/cpp.exe {defines} {Path(source).name} {stem}.i && '
                  f'{tool} {lib}/{cc1} -quiet {" ".join(flags)} {stem}.i -o {stem}.s && '
                  f'{tool} {lib}/as.exe {" ".join(f for f in flags if f.startswith(("-G", "-O")))} -EL {stem}.s -o {obj}')
        command = ['sh', '-c', script]
    else:
        command = ['/cc/bin/ee-gcc', '-c', *flags, Path(source).name, '-o', obj]
    ran = subprocess.run([docker, 'run', '--rm', '--network', 'none', '--platform', 'linux/386', *mounts,
                          '-w', '/w', IMAGE, *command], capture_output=True, text=True, timeout=300)
    (work / 'compile.log').write_text(' '.join(command) + '\n' + ran.stdout + ran.stderr)
    if ran.returncode or not (work / obj).is_file():
        raise RuntimeError('Compiler failed: ' + (ran.stderr.strip().splitlines() or ['no output'])[-1])
    return (work / obj).read_bytes()


def original_function(raw, name):
    """(bytes, address) of a function named in the executable's symbol table."""
    elf = rebuild.parse_elf(raw)
    for i, s in enumerate(elf['shdrs']):
        if s[1] != 2:
            continue
        strings = elf['shdrs'][s[6]]
        for offset in range(s[4], s[4] + s[5], 16):
            name_off, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', raw, offset)
            if info & 15 == 2 and size and raw[strings[4] + name_off:].split(b'\0', 1)[0].decode('latin-1') == name:
                section = elf['shdrs'][shndx]
                start = section[4] + value - section[3]
                return raw[start:start + size], value
    raise ValueError('Function not in the executable symbol table: ' + name)


def match(raw, function, source, compiler, work, flags=('-O2', '-G0')):
    obj = compile_source(compiler, source, work, flags)
    sections, functions, relocations = parse_object(obj)
    if function not in functions:
        raise ValueError(f'{function} not defined by {Path(source).name}')
    section, offset, size = functions[function]
    compiled = sections[section][offset:offset + (size or len(sections[section]) - offset)]
    relocs = [(o - offset, t) for o, t in relocations.get(section, []) if offset <= o < offset + len(compiled)]
    original, address = original_function(raw, function)
    same, rows = compare(original, compiled, relocs)
    return dict(function=function, address=address, compiler=Path(compiler).name, flags=list(flags), match=same,
                original_bytes=len(original), compiled_bytes=len(compiled),
                differing_words=sum(1 for r in rows if not r['same']), rows=rows)


def run(profile, private, function, source, compiler_id, name, flags):
    import pipeline as lab
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', name):
        raise ValueError('Invalid match name')
    private = Path(private).resolve()
    source = (private / lab.safe_relative(source)).resolve()
    if not source.is_relative_to(private) or not source.is_file():
        raise ValueError('Keep function sources in private game storage')
    available = compilers()
    if compiler_id not in available:
        raise ValueError('Unknown or unverified compiler; available: ' + ', '.join(available))
    work = private / 'matching' / name
    if work.exists():
        raise ValueError('Choose a new match name')
    raw = lab.boot_executable(private / 'disc-root', profile).read_bytes()
    report = match(raw, function, source, available[compiler_id], work, flags)
    report.update(local_only=True, retail_derived=True)
    (work / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    return report
