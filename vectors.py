"""Local vector compilation; captured code and generated sources stay private."""
from pathlib import Path
import json
import shutil
import tempfile
import time
import native
import pipeline as lab
import vu_dependencies as deps


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
    if 'dependency_report_sha256' in record:
        report=directory/'vu-dependencies.json'
        if report.is_symlink() or not report.is_file() or lab.digest(report)!=record['dependency_report_sha256']:
            raise ValueError('Compiled VU dependency report differs from its manifest')
        evidence=json.loads(report.read_text())
        if evidence.get('implementation')!=record.get('analysis_implementation'):
            raise ValueError('Compiled VU dependency analysis provenance differs')
        if {name:item['image_sha256'] for name,item in evidence.get('images',{}).items()}!=record.get('input_images'):
            raise ValueError('Compiled VU dependency report input identity differs')
    return directory,record


def compile_vu(profile,private,capture):
    capture=Path(capture).resolve()
    images=deps.capture_images(private,capture)
    snapshots={p.name:p.read_bytes() for p in images}
    inputs={name:deps.sha(data) for name,data in snapshots.items()}
    analysis_model=deps.implementation()
    compiler=private/'native-build/ps2PortVuRecompiler'
    if not compiler.is_file():
        raise ValueError('Run build-native to build the local vector compiler first')
    compiler_digest=lab.digest(compiler)
    output=private/'generated-vu'
    if output.exists():
        _,record=validated_sources(private)
        if record.get('input_images')==inputs and record.get('compiler_sha256')==compiler_digest and record.get('analysis_implementation')==analysis_model:
            print('Verified existing private compiled vector sources; no changes needed.')
            return record
        raise ValueError('Archive the existing generated-vu directory before replacing its source set')
    temporary=Path(tempfile.mkdtemp(prefix='vu-compile-',dir=private))
    try:
        if native.logged([compiler,capture,temporary],private/'logs/vu-compile.log')!='success':
            raise RuntimeError('Vector compiler did not complete successfully')
        # Diagnose the exact snapshots supplied to this compilation, without
        # changing generated code selection or declaring parallel eligibility.
        evidence=dict(schema=1,implementation=analysis_model,local_only=True,retail_derived=True,
                      images={name:deps.analyze_image(data) for name,data in snapshots.items()},
                      runtime_optimization_eligible=False)
        lab.save(temporary/'vu-dependencies.json',evidence)
        if any(lab.digest(path)!=inputs[path.name] for path in images):
            raise ValueError('Captured VU source changed during compilation')
        if lab.digest(compiler)!=compiler_digest:
            raise ValueError('VU compiler changed during compilation')
        (temporary/'register-local-vu.cpp').write_text('void registerGeneratedVu1Programs();\nnamespace { const bool registeredLocalVu = [] { registerGeneratedVu1Programs(); return true; }(); }\n')
        sources={p.name:lab.digest(p) for p in sorted(temporary.glob('*.cpp'))}
        if 'vu1_programs.cpp' not in sources or len(sources)<3:
            raise RuntimeError('Vector compiler did not produce a complete source set')
        record={'local_only':True,'retail_derived':True,'created_at':time.time(),
                'input_images':inputs,'compiler_sha256':compiler_digest,'sources':sources,
                'analysis_implementation':analysis_model,'dependency_report_sha256':lab.digest(temporary/'vu-dependencies.json')}
        lab.save(temporary/'manifest.json',record)
        temporary.rename(output)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    print(f'Compiled {len(images)} private vector images into {len(sources)} source files. Verify execution before packaging.')
    return record
