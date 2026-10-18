"""Resumable, local classified-texture SR pack; original guest textures stay intact."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

from local_sr import blend_upscale, combine, digest, infer_directory, prepare
from textures import decode, local_file
import texture_seams as seams


def policy_weight(mode):
    if mode not in {'full', 'detail-sensitive', 'preserve'}:
        raise ValueError('Every texture needs an explicit processing classification')
    return {'full': 1.0, 'detail-sensitive': 0.30, 'preserve': 0.0}[mode]


def gpu_idle():
    commands = subprocess.run(['/bin/ps', '-axo', 'comm'], check=True, capture_output=True, text=True).stdout
    if any('ps2EntryRunner' in line or 'realesrgan-ncnn-vulkan' in line or line.strip().endswith('/ninja') for line in commands.splitlines()):
        raise RuntimeError('A game, upscaler or native build is active; resume when GPU work is idle')


def save_json(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.replace(path)


def run(root, manifest_path, lock_path, output):
    from PIL import Image
    root = Path(root).resolve()
    manifest_path, lock_path = Path(manifest_path).resolve(), Path(lock_path).resolve()
    output = Path(output).absolute()
    if output.is_symlink() or not output.resolve().is_relative_to(root):
        raise ValueError('Pack must be inside the private game directory')
    output=output.resolve()
    manifest = json.loads(manifest_path.read_text())
    entries = manifest['textures']
    if manifest.get('schema') != 1 or not 1 <= len(entries) <= 4096:
        raise ValueError('Invalid or excessive texture manifest')
    identities = set()
    dimensions = {}
    sampling = {}
    families = {}
    # Preflight every source/baseline before any model execution.
    for entry in entries:
        if not re.fullmatch('[a-f0-9]{64}', entry['id']) or entry['id'] in identities:
            raise ValueError('Expected unique decoded-content hash IDs')
        identities.add(entry['id'])
        policy_weight(entry['mode'])
        if not entry.get('reason'):
            raise ValueError('Classification reason required')
        for field in ('source', 'baseline'):
            path = local_file(root, entry[field])
            if digest(path) != entry[field + '_sha256']:
                raise ValueError('Original or baseline hash mismatch')
        original = decode((root / entry['source']).read_bytes())
        if original.width * original.height > 262144:
            raise ValueError('Batch source exceeds 262,144 pixels')
        dimensions[entry['id']] = original.size
        sampling[entry['id']] = seams.policy(entry)
        family=entry.get('animation_family')
        if family is not None:
            if not isinstance(family,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',family):
                raise ValueError('Invalid animation family identifier')
            families.setdefault(family,[]).append(entry['id'])
    # A declared repeat axis applies to the entire animation family, including
    # high-contrast frames which a per-image heuristic might otherwise miss.
    for ids in families.values():
        axes=sorted(set().union(*(sampling[i]['axes'] for i in ids)))
        padding=max(sampling[i]['padding'] for i in ids)
        for i in ids: sampling[i]=dict(axes=axes,padding=padding)
    for entry in entries:
        w,h=dimensions[entry['id']]; sample=sampling[entry['id']]
        padded_w=w+(2*sample['padding'] if 'x' in sample['axes'] else 0)
        padded_h=h+(2*sample['padding'] if 'y' in sample['axes'] else 0)
        if sample['axes'] and (max(padded_w,padded_h)>1024 or padded_w*padded_h>1024*1024):
            raise ValueError('Periodic texture exceeds bounded context dimensions')
    pairs=manifest.get('seam_pairs',[])
    if not isinstance(pairs,list) or len(pairs)>4096: raise ValueError('Invalid seam-pair list')
    for pair in pairs:
        if not isinstance(pair,dict) or any(not isinstance(pair.get(k),str) or pair[k] not in identities for k in ('a','b')):
            raise ValueError('Seam pair references unknown texture')
        if any(pair.get(k) not in {'left','right','top','bottom'} for k in ('edge_a','edge_b')) or type(pair.get('reverse',False)) is not bool or not pair.get('evidence'):
            raise ValueError('Seam pairs require explicit edges, orientation and adjacency evidence')
        a=dimensions[pair['a']]; b=dimensions[pair['b']]
        if a[1 if pair['edge_a'] in ('left','right') else 0]!=b[1 if pair['edge_b'] in ('left','right') else 0]:
            raise ValueError('Declared seam edges have different lengths')
    output.mkdir(exist_ok=True)
    with (output / '.lock').open('a') as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report_path = output / 'pack-index.json'
        pins = {'manifest_sha256': digest(manifest_path), 'model_lock_sha256': digest(lock_path),
                'engine_sha256': digest(Path(__file__)), 'sr_engine_sha256': digest(Path(__file__).with_name('local_sr.py')),
                'seam_engine_sha256': digest(Path(seams.__file__))}
        if report_path.exists():
            report = json.loads(report_path.read_text())
            if report['pins'] != pins:
                raise ValueError('Resume pins do not match the existing run')
        else:
            report = {'schema': 1, 'pins': pins, 'status': 'processing', 'local_only': True,
                      'runtime_integration': 'pending', 'review_required': True,
                      'mip_policy': 'enhanced level0 only; runtime mip/atlas policy pending', 'textures': []}
        completed = {entry['id']: entry for entry in report['textures']}
        report.pop('error', None)
        report['status'] = 'processing'
        for entry in completed.values():
            if digest(local_file(root, entry['file'])) != entry['sha256']:
                raise ValueError('Completed output changed; refusing to resume')
        # An abrupt exit can commit a PNG before its index. Preserve that file,
        # then recompute from pinned inputs; never silently adopt an orphan.
        for entry in entries:
            file = output / (entry['id'] + '.png')
            if entry['id'] not in completed and file.exists():
                if file.is_symlink() or not file.is_file():
                    raise ValueError('Unsafe unindexed output')
                orphan_dir = output / 'orphaned'
                if orphan_dir.is_symlink():
                    raise ValueError('Unsafe recovery directory')
                orphan_dir.mkdir(exist_ok=True)
                saved = orphan_dir / (entry['id'] + '-' + uuid.uuid4().hex + '.png')
                file.replace(saved)
        try:
            pending = []
            for entry in entries:
                if entry['id'] in completed:
                    continue
                if entry['mode'] == 'preserve' and not sampling[entry['id']]['axes']:
                    report['textures'].append({'id': entry['id'], 'mode': entry['mode'], 'ai_weight': 0,
                                              'reason': entry['reason'], 'file': entry['baseline'],
                                              'sha256': entry['baseline_sha256']})
                else:
                    pending.append(entry)
            save_json(report_path, report)
            groups=[]
            for periodic in (True,False):
                selected=[e for e in pending if bool(sampling[e['id']]['axes'])==periodic]
                groups.extend(selected[start:start+16] for start in range(0,len(selected),16))
            for group in groups:
                needs_ai=any(e['mode']!='preserve' for e in group)
                if needs_ai: gpu_idle()
                # Reserve 1 GiB plus conservative scratch/output space for this group.
                pixels = sum((dimensions[e['id']][0]+(2*sampling[e['id']]['padding'] if 'x' in sampling[e['id']]['axes'] else 0)) * (dimensions[e['id']][1]+(2*sampling[e['id']]['padding'] if 'y' in sampling[e['id']]['axes'] else 0)) for e in group)
                if shutil.disk_usage(output).free < 1024**3 + pixels * 16 * 12:
                    raise OSError('Insufficient free space for the next bounded batch')
                with tempfile.TemporaryDirectory(prefix='.sr-scratch-', dir=output) as scratch:
                    scratch = Path(scratch)
                    inputs, predictions = scratch / 'inputs', scratch / 'predictions'
                    inputs.mkdir(); predictions.mkdir()
                    originals = {}
                    for entry in group:
                        original = decode((root / entry['source']).read_bytes())
                        originals[entry['id']] = original
                        sampling_policy=sampling[entry['id']]
                        context=seams.pad(original,sampling_policy['axes'],sampling_policy['padding']) if sampling_policy['axes'] else original
                        if entry['mode']!='preserve': prepare(context).save(inputs / (entry['id'] + '.png'))
                    number = len(list(output.glob('inference-*.log'))) + 1
                    if needs_ai:
                        if any(sampling[e['id']]['axes'] for e in group):
                            contexts=[seams.pad(originals[e['id']],sampling[e['id']]['axes'],sampling[e['id']]['padding']) for e in group if e['mode']!='preserve']
                            infer_directory(lock_path, inputs, predictions, output / f'inference-{number:04d}.log',tile=seams.tile_size(contexts))
                        else:
                            infer_directory(lock_path, inputs, predictions, output / f'inference-{number:04d}.log')
                    for entry in group:
                        original = originals[entry['id']]
                        sampling_policy=sampling[entry['id']]
                        axes,padding=sampling_policy['axes'],sampling_policy['padding']
                        if axes:
                            faithful=seams.baseline(original,axes,padding)
                        else:
                            with Image.open(root / entry['baseline']) as image: faithful=image.convert('RGBA')
                        if entry['mode']=='preserve':
                            enhanced=faithful
                        else:
                            with Image.open(predictions / (entry['id'] + '.png')) as prediction:
                                inferred=seams.finish(prediction,original,axes,padding) if axes else combine(prediction,original)
                            enhanced=blend_upscale(faithful,inferred,policy_weight(entry['mode']))
                        file = output / (entry['id'] + '.png')
                        if file.exists():
                            raise ValueError('Unindexed output exists; refusing to overwrite')
                        if digest(root / entry['source']) != entry['source_sha256']:
                            raise ValueError('Source changed during inference')
                        enhanced.save(file)
                        report['textures'].append({'id': entry['id'], 'mode': entry['mode'],
                                                  'ai_weight': policy_weight(entry['mode']), 'reason': entry['reason'],
                                                  'file': str(file.relative_to(root)), 'sha256': digest(file),
                                                  'width': enhanced.width, 'height': enhanced.height, 'sampling':sampling_policy})
                        save_json(report_path, report)
                print(f"Completed {len(report['textures'])}/{len(entries)} textures", flush=True)
            for entry in entries:
                for field in ('source','baseline'):
                    if digest(local_file(root,entry[field]))!=entry[field+'_sha256']:
                        raise ValueError('Texture input changed during processing')
            saved={entry['id']:entry for entry in report['textures']}
            originals={entry['id']:entry for entry in entries}
            checks=[]
            def source_image(tid): return decode(local_file(root,originals[tid]['source']).read_bytes())
            def output_image(tid):
                with Image.open(local_file(root,saved[tid]['file'])) as image: return image.convert('RGBA')
            for entry in entries:
                tid=entry['id']
                for axis in sampling[tid]['axes']:
                    a,b=('right','left') if axis=='x' else ('bottom','top')
                    original=source_image(tid); enhanced=output_image(tid)
                    checks.append(dict(kind='repeat',a=tid,b=tid,axis=axis,
                        **seams.check_pair(original,a,original,b,enhanced,enhanced)))
            for pair in pairs:
                checks.append(dict(kind='adjacency',a=pair['a'],b=pair['b'],evidence=pair['evidence'],
                    **seams.check_pair(source_image(pair['a']),pair['edge_a'],source_image(pair['b']),pair['edge_b'],
                                       output_image(pair['a']),output_image(pair['b']),pair.get('reverse',False))))
            report['seam_validation']=dict(status='passed_declared_edges' if checks and all(c['passed'] for c in checks) else 'failed' if checks else 'not_declared',
                checks=checks, undeclared_adjacencies_verified=False, mipmaps_verified=False)
            report['status'] = 'blocked_seam_review' if any(not c['passed'] for c in checks) else 'processed_requires_visual_review'
            if len(report['textures']) != len(entries):
                raise RuntimeError('Final texture coverage mismatch')
        except Exception as error:
            report['status'] = 'interrupted_resumable'
            report['error'] = str(error)
            raise
        finally:
            save_json(report_path, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report=run(args.root, args.manifest, args.lock, args.output)
    if report['status']=='blocked_seam_review': raise SystemExit(2)
