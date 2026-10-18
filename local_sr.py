"""Bounded, offline Real-ESRGAN preview backend; no renderer integration."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_file(root, item):
    relative = Path(item['file'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Tool paths must be relative to the lock file')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Tool must be a regular file inside the locked directory')
    if digest(path) != item['sha256']:
        raise ValueError('Pinned executable/model hash mismatch')
    return path.resolve()


def prepare(image):
    """Never train/infer alpha or feed invisible arbitrary RGB to the model.

    Composite over neutral gray for this preview. Partly transparent materials
    need separate qualification; this policy is not approved for a game pack.
    """
    from PIL import Image
    rgba = image.convert('RGBA')
    return Image.alpha_composite(Image.new('RGBA', rgba.size, (128, 128, 128, 255)), rgba).convert('RGB')


def combine(predicted, original):
    from PIL import Image
    size = (original.width * 4, original.height * 4)
    if predicted.size != size:
        raise ValueError('Model output must be exactly 4x source dimensions')
    result = predicted.convert('RGBA')
    result.putalpha(original.convert('RGBA').getchannel('A').resize(size, Image.Resampling.LANCZOS))
    return result


def blend_upscale(base, inferred, ai_weight=0.30):
    """Blend matching decoded RGBA outputs, preserving source-derived alpha.

    This deliberately matches the channel-linear clock previews. It reduces
    inferred details without claiming to correct invented lettering or colors.
    """
    from PIL import Image
    import math
    if isinstance(ai_weight, bool) or not isinstance(ai_weight, (int, float)) or not math.isfinite(ai_weight) or not 0 <= ai_weight <= 1:
        raise ValueError('AI weight must be a finite number from 0 to 1')
    base, inferred = base.convert('RGBA'), inferred.convert('RGBA')
    if base.size != inferred.size or base.getchannel('A').tobytes() != inferred.getchannel('A').tobytes():
        raise ValueError('Blend inputs must have matching dimensions and source-derived alpha')
    return Image.blend(base, inferred, ai_weight)


def infer_directory(lock_path, inputs, outputs, log_path, timeout=600, reserve_bytes=1024**3, tile=128):
    """One pinned local model process for a bounded prepared-PNG directory.

    `tile` is the model's internal tile size in input pixels. Inputs larger than one tile are
    processed in pieces whose joins can show; wrap-padded seamless inputs pass a larger tile.
    """
    if type(tile) is not int or not 32 <= tile <= 1024:
        raise ValueError('Tile size must be 32..1024 input pixels')
    lock_path = Path(lock_path).resolve()
    lock = json.loads(lock_path.read_text())
    if lock.get('schema') != 1 or lock.get('model') != 'realesrgan-x4plus':
        raise ValueError('Expected the pinned general x4plus model')
    executable = pinned_file(lock_path.parent, lock['executable'])
    models = [pinned_file(lock_path.parent, item) for item in lock['models']]
    if len(models) != 2 or {p.name for p in models} != {'realesrgan-x4plus.bin', 'realesrgan-x4plus.param'} or models[0].parent != models[1].parent:
        raise ValueError('Expected matching model parameters/weights')
    inputs, outputs = Path(inputs).resolve(), Path(outputs).resolve()
    if not 1 <= len(list(inputs.iterdir())) <= 16:
        raise ValueError('Inference directory must contain 1..16 prepared PNGs')
    command = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)',
               str(executable), '-i', str(inputs), '-o', str(outputs), '-m', str(models[0].parent),
               '-n', lock['model'], '-s', '4', '-t', str(tile), '-j', '1:1:1', '-f', 'png']
    with Path(log_path).open('xb') as log:
        process = subprocess.Popen(command, cwd=executable.parent, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Local inference batch exceeded its time bound')
                if shutil.disk_usage(outputs).free < reserve_bytes:
                    raise OSError('Local inference stopped at the free-space reserve')
                time.sleep(0.25)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def preview(lock_path, sources, destination, timeout=180, ai_weight=1.0):
    from PIL import Image
    from textures import resize
    # Validate before creating a run or performing inference.
    blend_upscale(Image.new('RGBA', (1, 1)), Image.new('RGBA', (1, 1)), ai_weight)
    lock_path = Path(lock_path).resolve()
    lock = json.loads(lock_path.read_text())
    if lock.get('schema') != 1 or lock.get('model') != 'realesrgan-x4plus':
        raise ValueError('Only the pinned general x4plus preview model is qualified here')
    executable = pinned_file(lock_path.parent, lock['executable'])
    models = [pinned_file(lock_path.parent, item) for item in lock['models']]
    expected = {'realesrgan-x4plus.bin', 'realesrgan-x4plus.param'}
    if {p.name for p in models} != expected or len(models) != 2 or models[0].parent != models[1].parent:
        raise ValueError('Expected the matching model parameter and weight files')
    if not 1 <= len(sources) <= 16:
        raise ValueError('Preview accepts 1..16 PNG files')
    decoded = []
    for source in sources:
        source = Path(source).resolve()
        raw = source.read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Input exceeds encoded byte limit')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1 or image.width * image.height > 65536:
                raise ValueError('Preview requires single PNGs of at most 65,536 pixels')
            decoded.append((source, hashlib.sha256(raw).hexdigest(), image.convert('RGBA')))
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Preview destination must be new')
    if shutil.disk_usage(destination.parent).free < 1024 * 1024 * 1024:
        raise ValueError('At least 1 GiB free space is required')
    # Independent of Python: forbid network access in the external executable.
    sandbox = Path('/usr/bin/sandbox-exec')
    if not sandbox.is_file():
        raise RuntimeError('macOS network-denying sandbox-exec is required')
    destination.mkdir()
    report = {'schema': 1, 'status': 'running', 'model': lock['model'],
              'lock_sha256': digest(lock_path), 'engine_sha256': digest(Path(__file__)),
              'runtime_integration': 'pending', 'review_required': True,
              'ai_weight': ai_weight, 'blend': 'channel-linear RGBA; matching source alpha',
              'alpha_policy': 'source-lanczos; gray-composite RGB preview only',
              'network': 'denied to external inference process', 'entries': []}
    report_path = destination / 'report.json'
    try:
        for number, (source, source_hash, original) in enumerate(decoded, 1):
            prepared = destination / f'input-{number}.png'
            prediction = destination / f'prediction-{number}.png'
            output = destination / f'enhanced-{number}.png'
            prepare(original).save(prepared)
            command = [str(sandbox), '-p', '(version 1)(allow default)(deny network*)',
                       str(executable), '-i', str(prepared), '-o', str(prediction),
                       '-m', str(models[0].parent), '-n', lock['model'], '-s', '4',
                       '-t', '128', '-j', '1:1:1', '-f', 'png']
            started = time.monotonic()
            with (destination / f'inference-{number}.log').open('wb') as log:
                subprocess.run(command, cwd=executable.parent, stdout=log, stderr=subprocess.STDOUT,
                               timeout=timeout, check=True)
            with Image.open(prediction) as predicted:
                inferred = combine(predicted, original)
                baseline = resize(original, inferred.size, 'clamp', 'smooth')
                blend_upscale(baseline, inferred, ai_weight).save(output)
            if digest(source) != source_hash:
                raise RuntimeError('Source changed during inference')
            report['entries'].append({'source': str(source), 'source_sha256': source_hash,
                                      'output': output.name, 'output_sha256': digest(output),
                                      'seconds': time.monotonic() - started})
        report['status'] = 'processed_preview_requires_review'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--detail-sensitive', action='store_true',
                        help='Lettering, numbers or important fine details: use 30%% AI blend')
    parser.add_argument('--ai-weight', type=float, default=None,
                        help='Explicit comparison override; otherwise full AI, or 0.30 with --detail-sensitive')
    parser.add_argument('sources', nargs='+')
    args = parser.parse_args()
    weight = args.ai_weight if args.ai_weight is not None else (0.30 if args.detail_sensitive else 1.0)
    print(json.dumps(preview(args.lock, args.sources, args.output, ai_weight=weight), indent=2))
