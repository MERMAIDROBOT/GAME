"""Bounded, private VU1 dependency reports; never enable runtime optimizations.

Reports describe the reviewed decoder's model. They do not certify hardware
behavior, memory independence, termination, arbitrary entry points or scheduling.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import vu_dependency_core as core

ROOT=Path(__file__).resolve().parent
SOURCE_PIN='b65592e383703e6def6e840f289681ba512a8903'
MAX_IMAGES=128
MAX_ENTRIES=64


def sha(data):return hashlib.sha256(data).hexdigest()


def implementation():
    return dict(schema=1,upstream='DavvidMac/PS2Recomp-DevilKings',revision=SOURCE_PIN,
                source='ps2xRuntime/tools/vu1rec.py',
                hashes={name:sha((ROOT/name).read_bytes()) for name in ('vu_dependencies.py','vu_dependency_core.py')},
                per_entry_budget=4096,total_budget=20000,max_entries=MAX_ENTRIES,
                runtime_optimization_eligible=False)


def input_record(key):
    result={'kind':key[0]}
    if key[0] in ('vf','vi'):result['register']=key[1]
    if key[0]=='vf':result['lane']='xyzw'[key[2]]
    if key[0]=='acc':result['lane']='xyzw'[key[1]]
    if key[0]=='clip':result['mask']=f'{key[1]:06x}'
    return result


def analyze_image(image,per_entry_budget=4096,total_budget=20000):
    if not isinstance(image,bytes) or len(image)!=16384:
        raise ValueError('Expected exactly 16 KiB of VU1 micro memory')
    if any(type(v) is not int or not 0<=v<=100000 for v in (per_entry_budget,total_budget)):
        raise ValueError('VU dependency budgets must be integers from 0 to 100000')
    report=dict(schema=1,image_sha256=sha(image),bytes=len(image),entries=[],
                status='unknown',runtime_optimization_eligible=False,
                memory_dependencies='not_analyzed',hardware_verified=False,
                arbitrary_entry_coverage=False,states_examined=0,
                limits=dict(per_entry_states=per_entry_budget,total_states=total_budget,max_entries=MAX_ENTRIES))
    try:
        labels,pairs=core.analyse(image)
    except core.Reject as error:
        return dict(report,status='unsupported',reason=str(error))
    report['reachable_pairs']=len(labels)
    report['decoded_pairs']=len(pairs)
    report['decoded_pc_in_bounds']=all(0<=pc<16384 and pc%8==0 for pc in pairs)
    if not report['decoded_pc_in_bounds']:
        raise RuntimeError('Dependency decoder escaped VU micro memory')
    # The borrowed analyzer assumes indirect branches reach known return points.
    # Do not expose that assumption as proven coverage for arbitrary guest code.
    if any(p.is_branch and p.op_hi in (0x24,0x25) for p in pairs.values()):
        return dict(report,status='control_flow_unverified',reason='indirect_branch_targets_not_qualified')
    if any(not p.i_bit and p.op_hi==0x15 for p in pairs.values()):
        return dict(report,status='unsupported',reason='sticky_status_write_not_qualified')
    roots=sorted(({0}|{(pc+16)&0x3fff for pc,p in labels.items() if p.e_bit}) & labels.keys())
    if not roots:
        return dict(report,status='unknown',reason='no_discovered_entries')
    if len(roots)>MAX_ENTRIES:
        return dict(report,status='budget_exhausted',reason='entry_count_limit',discovered_entries=len(roots))
    remaining=total_budget
    for entry in roots:
        metrics={}
        inputs=core.entry_inputs(labels,pairs,entry,limit=min(per_entry_budget,remaining),metrics=metrics)
        used=metrics.get('visited_states',0)
        remaining-=used;report['states_examined']+=used
        item=dict(pc=f'{entry:04x}',states_examined=used)
        if inputs is None:
            item.update(status='unknown',reason='analysis_budget_or_missing_control_flow',inputs=None)
        else:
            item.update(status='modeled',inputs=[input_record(k) for k in sorted(inputs,key=str)])
        report['entries'].append(item)
    report['status']='modeled' if all(x['status']=='modeled' for x in report['entries']) else 'incomplete'
    report['entry_discovery']='PC0 and discovered E-bit resume points only'
    report['termination_verified']=False
    return report


def capture_images(private,capture):
    private=Path(private).resolve();capture=Path(capture).resolve()
    if not capture.is_relative_to(private) or not capture.is_dir():
        raise ValueError('Keep captured vector images inside private game storage')
    images=sorted(capture.glob('vu1_*.bin'))
    if not images or len(images)>MAX_IMAGES:
        raise ValueError(f'Expected 1 to {MAX_IMAGES} captured VU1 images')
    for path in images:
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=16384:
            raise ValueError('Expected complete 16 KiB images without symlinks')
    return images


def analyze_capture(private,capture,name):
    if not re.fullmatch('[a-z0-9][a-z0-9_-]*',name):
        raise ValueError('Invalid dependency report name')
    paths=capture_images(private,capture)
    images={p.name:analyze_image(p.read_bytes()) for p in paths}
    target=Path(private)/'analysis/vu-dependencies'/name
    # Resolve containment before creation as well as checking each image.
    if not target.resolve().is_relative_to(Path(private).resolve()):
        raise ValueError('Dependency output escapes private storage')
    target.mkdir(parents=True,mode=0o700,exist_ok=False)
    os.chmod(target,0o700)
    report=dict(schema=1,local_only=True,retail_derived=True,implementation=implementation(),images=images,
                runtime_optimization_eligible=False)
    with (target/'report.json').open('x') as handle:
        json.dump(report,handle,indent=2);handle.write('\n')
    return report
