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
    manifest = json.loads(manifest_path.read_text())
    entries = manifest['textures']
    if manifest.get('schema') != 1 or not 1 <= len(entries) <= 4096:
        raise ValueError('Invalid or excessive texture manifest')
    identities = set()
    dimensions = {}
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
    output.mkdir(exist_ok=True)
    with (output / '.lock').open('a') as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report_path = output / 'pack-index.json'
        pins = {'manifest_sha256': digest(manifest_path), 'model_lock_sha256': digest(lock_path),
                'engine_sha256': digest(Path(__file__)), 'sr_engine_sha256': digest(Path(__file__).with_name('local_sr.py'))}
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
                if entry['mode'] == 'preserve':
                    report['textures'].append({'id': entry['id'], 'mode': entry['mode'], 'ai_weight': 0,
                                              'reason': entry['reason'], 'file': entry['baseline'],
                                              'sha256': entry['baseline_sha256']})
                else:
                    pending.append(entry)
            save_json(report_path, report)
            for start in range(0, len(pending), 16):
                gpu_idle()
                group = pending[start:start + 16]
                # Reserve 1 GiB plus conservative scratch/output space for this group.
                pixels = sum(dimensions[e['id']][0] * dimensions[e['id']][1] for e in group)
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
                        prepare(original).save(inputs / (entry['id'] + '.png'))
                    number = len(list(output.glob('inference-*.log'))) + 1
                    infer_directory(lock_path, inputs, predictions, output / f'inference-{number:04d}.log')
                    for entry in group:
                        original = originals[entry['id']]
                        with Image.open(predictions / (entry['id'] + '.png')) as prediction:
                            inferred = combine(prediction, original)
                        with Image.open(root / entry['baseline']) as baseline:
                            enhanced = blend_upscale(baseline, inferred, policy_weight(entry['mode']))
                        file = output / (entry['id'] + '.png')
                        if file.exists():
                            raise ValueError('Unindexed output exists; refusing to overwrite')
                        if digest(root / entry['source']) != entry['source_sha256']:
                            raise ValueError('Source changed during inference')
                        enhanced.save(file)
                        report['textures'].append({'id': entry['id'], 'mode': entry['mode'],
                                                  'ai_weight': policy_weight(entry['mode']), 'reason': entry['reason'],
                                                  'file': str(file.relative_to(root)), 'sha256': digest(file),
                                                  'width': enhanced.width, 'height': enhanced.height})
                        save_json(report_path, report)
                print(f"Completed {len(report['textures'])}/{len(entries)} textures", flush=True)
            report['status'] = 'processed_requires_visual_review'
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
    run(args.root, args.manifest, args.lock, args.output)
