"""Local vector compilation; captured code and generated sources stay private."""
from pathlib import Path
import json
import shutil
import tempfile
import time
import native
import pipeline as lab


def validated_sources(private):
    directory=private/'generated-vu'
    if directory.is_symlink() or not directory.resolve().is_relative_to(private.resolve()):
        raise ValueError('Keep compiled vector sources in private game storage')
    record=json.loads((directory/'manifest.json').read_text())
    sources=record.get('sources',{})
    if not sources or set(sources)!={p.name for p in directory.glob('*.cpp')}:
        raise ValueError('Compiled vector source set differs from its manifest')
    for name,digest in sources.items():
        p=directory/name
        if Path(name).name!=name or p.suffix!='.cpp' or p.is_symlink() or lab.digest(p)!=digest:
            raise ValueError('Compiled vector source differs from its manifest')
    if directory.is_symlink() or not directory.resolve().is_relative_to(private.resolve()):
        raise ValueError('Keep compiled vector sources in private game storage')
    return directory,record


def compile_vu(profile,private,capture):
    capture=Path(capture).resolve()
    if not capture.is_relative_to(private.resolve()):
        raise ValueError('Keep captured vector images in private game storage')
    images=sorted(capture.glob('vu1_*.bin'))
    if not images or any(p.is_symlink() or p.stat().st_size!=16384 for p in images):
        raise ValueError('Expected complete 16 KiB captured vector images without symlinks')
    inputs={p.name:lab.digest(p) for p in images}
    compiler=private/'native-build/ps2PortVuRecompiler'
    if not compiler.is_file():
        raise ValueError('Run build-native to build the local vector compiler first')
    output=private/'generated-vu'
    if output.exists():
        _,record=validated_sources(private)
        if record.get('input_images')==inputs and record.get('compiler_sha256')==lab.digest(compiler):
            print('Verified existing private compiled vector sources; no changes needed.')
            return record
        raise ValueError('Archive the existing generated-vu directory before replacing its source set')
    temporary=Path(tempfile.mkdtemp(prefix='vu-compile-',dir=private))
    try:
        if native.logged([compiler,capture,temporary],private/'logs/vu-compile.log')!='success':
            raise RuntimeError('Vector compiler did not complete successfully')
        (temporary/'register-local-vu.cpp').write_text('void registerGeneratedVu1Programs();\nnamespace { const bool registeredLocalVu = [] { registerGeneratedVu1Programs(); return true; }(); }\n')
        sources={p.name:lab.digest(p) for p in sorted(temporary.glob('*.cpp'))}
        if 'vu1_programs.cpp' not in sources or len(sources)<3:
            raise RuntimeError('Vector compiler did not produce a complete source set')
        record={'local_only':True,'retail_derived':True,'created_at':time.time(),
                'input_images':inputs,'compiler_sha256':lab.digest(compiler),'sources':sources}
        lab.save(temporary/'manifest.json',record)
        temporary.rename(output)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    print(f'Compiled {len(images)} private vector images into {len(sources)} source files. Verify execution before packaging.')
    return record
