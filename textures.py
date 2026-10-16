"""Local deterministic enhancement of decoded textures; no network or game loader."""
from pathlib import Path
import hashlib
import io
import json
import warnings
import shutil

ALGORITHM = 'premultiplied-lanczos-periodic-v1'
MAX_PIXELS = 1024 * 1024
MAX_TOTAL_PIXELS = 8 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def local_file(root, relative):
    from pipeline import safe_relative
    path = root / safe_relative(relative)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('Texture input must be a regular private local file')
    return path


def decode(raw):
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Extractor must provide a single decoded PNG')
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Texture exceeds input pixel bound')
            image.load()
            return image.convert('RGBA')


def resize(image, size, wrap, alpha):
    """Filter premultiplied color; periodic padding includes opposite-edge neighbors."""
    from PIL import Image
    original = image
    if wrap == 'repeat':
        tiled = Image.new('RGBA', (image.width * 3, image.height * 3))
        for y in range(3):
            for x in range(3):
                tiled.paste(image, (x * image.width, y * image.height))
        image = tiled
        target = (size[0] * 3, size[1] * 3)
    else:
        target = size
    result = image.convert('RGBa').resize(target, Image.Resampling.LANCZOS).convert('RGBA')
    if wrap == 'repeat':
        result = result.crop((size[0], size[1], size[0] * 2, size[1] * 2))
    if alpha == 'cutout':
        result.putalpha(original.getchannel('A').resize(size, Image.Resampling.NEAREST))
    return result



def refine_detail(image, wrap='clamp', strength=0.85, radius=0.75, neighborhood=1):
    """Bounded luminance sharpening in source space; alpha and flat colors preserved.

    Alpha-normalized blur prevents hidden RGB from leaking into visible edges.
    Local color bounds limit ringing. This restores contrast, not missing detail.
    """
    from PIL import Image, ImageFilter
    import numpy as np
    rgba = np.asarray(image.convert('RGBA'), dtype=np.float32)
    if wrap not in {'clamp','repeat'} or not 0 <= strength <= 1.5 or not 0.25 <= radius <= 3 or type(neighborhood) is not int or not 1 <= neighborhood <= 4:
        raise ValueError('Invalid detail policy')
    if strength == 0:
        return image.copy()
    pad = max(4, int(radius*4)+1, neighborhood)
    padded = np.pad(rgba, ((pad,pad),(pad,pad),(0,0)), mode='wrap' if wrap=='repeat' else 'edge')
    # Pillow performs deterministic Gaussian filtering on premultiplied channels.
    premult = Image.fromarray(padded.astype('uint8')).convert('RGBa')
    blurred = np.asarray(premult.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)[pad:-pad,pad:-pad]
    alpha = rgba[:,:,3:4]
    soft_rgb = blurred[:,:,:3] * 255.0 / np.maximum(blurred[:,:,3:4], 1.0)
    luminance = np.array([0.2126,0.7152,0.0722], dtype=np.float32)
    detail = (rgba[:,:,:3] - soft_rgb) @ luminance
    # Soft threshold suppresses quantization noise; bound brightness correction.
    detail = np.sign(detail) * np.maximum(np.abs(detail)-1.5,0.0)
    detail = np.clip(detail*strength,-18.0,18.0)
    minimum = rgba[:,:,:3].copy(); maximum = minimum.copy()
    for dy in range(-neighborhood,neighborhood+1):
        for dx in range(-neighborhood,neighborhood+1):
            neighbor = padded[pad+dy:pad+dy+image.height,pad+dx:pad+dx+image.width]
            visible = neighbor[:,:,3:4] >= 16
            minimum = np.minimum(minimum,np.where(visible,neighbor[:,:,:3],minimum))
            maximum = np.maximum(maximum,np.where(visible,neighbor[:,:,:3],maximum))
    rgb = np.clip(rgba[:,:,:3] + detail[:,:,None], minimum, maximum)
    rgba[:,:,:3] = np.where(alpha>=16,rgb,rgba[:,:,:3])
    return Image.fromarray(np.rint(rgba).clip(0,255).astype('uint8'))



def postblend_detail(image, wrap='clamp', scale=4, amount=0.65):
    """Sharpen the enlarged image, then blend by local edge contrast.

    RGB-only blend leaves the enlarged alpha byte-identical. Smooth areas get
    little treatment; stronger edges receive at most the requested blend amount.
    """
    import numpy as np
    from PIL import Image
    if not 0 <= amount <= 1 or type(scale) is not int or scale not in (1,2,4):
        raise ValueError('Invalid postblend policy')
    base=np.asarray(image.convert('RGBA'),dtype=np.float32)
    sharp=np.asarray(refine_detail(image,wrap,strength=1.3,
        radius=max(0.75,scale*0.5),neighborhood=min(4,scale)),dtype=np.float32)
    luma=base[:,:,:3] @ np.array([0.2126,0.7152,0.0722],dtype=np.float32)
    padded=np.pad(luma,1,mode='wrap' if wrap=='repeat' else 'edge')
    padded_alpha=np.pad(base[:,:,3],1,mode='wrap' if wrap=='repeat' else 'edge')
    low=luma.copy();high=luma.copy()
    for y in range(3):
        for x in range(3):
            neighbor=padded[y:y+image.height,x:x+image.width]
            visible=padded_alpha[y:y+image.height,x:x+image.width]>=16
            low=np.minimum(low,np.where(visible,neighbor,low));high=np.maximum(high,np.where(visible,neighbor,high))
    weight=amount*np.clip((high-low)/24.0,0,1)*(base[:,:,3]>=16)
    base[:,:,:3]+=(sharp[:,:,:3]-base[:,:,:3])*weight[:,:,None]
    return Image.fromarray(np.rint(base).clip(0,255).astype('uint8'))


def encode(image):
    data = io.BytesIO()
    image.save(data, format='PNG', compress_level=9, optimize=False)
    return data.getvalue()


def enhance(profile, private, manifest, name):
    from PIL import __version__ as pillow_version
    from compiler import identifier
    from pipeline import boot_executable, digest, save
    identifier(name)
    private = Path(private).resolve()
    spec_path = local_file(private, manifest)
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    if spec.get('schema') != 1 or spec.get('title_id') != profile['id']:
        raise ValueError('Texture manifest schema/title mismatch')
    identity = digest(boot_executable(private / 'disc-root', profile))
    if identity != profile.get('expected_sha256') or spec.get('executable_sha256') != identity:
        raise ValueError('Texture manifest disc revision mismatch')
    entries = spec.get('textures')
    if not isinstance(entries, list) or not entries or len(entries) > 1024:
        raise ValueError('Texture manifest needs 1..1024 entries')
    min_free=spec.get('min_free_bytes',256*1024*1024)
    if type(min_free) is not int or not 256*1024*1024<=min_free<=8*1024**3:
        raise ValueError('Invalid free-space reserve')
    prepared, seen, total = [], set(), 0
    for entry in entries:
        tid = entry['id']; identifier(tid)
        if tid in seen:
            raise ValueError('Duplicate texture ID')
        seen.add(tid)
        path = local_file(private, entry['file'])
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Texture encoded input exceeds bound')
        raw = path.read_bytes()
        if sha(raw) != entry['sha256']:
            raise ValueError('Texture source hash mismatch')
        image = decode(raw)
        role = entry.get('role', 'unclassified')
        if role not in {'world', 'ui', 'text', 'unclassified'}:
            raise ValueError('Unknown texture role')
        wrap, alpha = entry.get('wrap', 'clamp'), entry.get('alpha', 'smooth')
        scale = entry.get('scale', 2)
        if wrap not in {'clamp', 'repeat'} or alpha not in {'smooth', 'cutout'}:
            raise ValueError('Unsupported texture sampling policy')
        if type(scale) is not int or scale not in {1, 2, 4}:
            raise ValueError('Texture scale must be 1, 2 or 4')
        # Unknown semantics remain flagged even when explicitly batch-resampled.
        unclassified_filter=entry.get('unclassified_filter','preserve')
        if unclassified_filter not in {'preserve','lanczos'} or (unclassified_filter!='preserve' and role!='unclassified'):
            raise ValueError('Unclassified filter requires an unclassified texture')
        # UI/text/unclassified require explicit policy before resampling.
        ui_filter = entry.get('ui_filter', 'preserve')
        if ui_filter not in {'preserve', 'nearest'} or (ui_filter == 'nearest' and role not in {'ui','text'}):
            raise ValueError('UI filter requires a classified text/UI texture')
        if role != 'world' and ui_filter == 'preserve' and unclassified_filter=='preserve':
            scale = 1
        if role in {'text','ui'} and wrap != 'clamp':
            raise ValueError('Text/UI textures must clamp at their boundaries')
        if image.width * image.height * scale * scale > MAX_PIXELS * 4:
            raise ValueError('Enhanced texture exceeds output bound')
        total += image.width * image.height
        if total > MAX_TOTAL_PIXELS:
            raise ValueError('Batch input pixel budget exceeded')
        detail = entry.get('detail', 'none')
        if detail not in {'none','adaptive','postblend'} or (detail!='none' and role!='world'):
            raise ValueError('Adaptive detail currently requires a world texture')
        if detail!='none':
            import numpy as np
            numpy_version=np.__version__
        else:
            numpy_version=None
        policy = dict(detail=detail,numpy=numpy_version,algorithm=ALGORITHM, engine_sha256=sha(Path(__file__).read_bytes()), pillow=pillow_version, scale=scale,
                      wrap=wrap, alpha=alpha, mipmaps=role == 'world' or unclassified_filter=='lanczos', role=role, ui_filter=ui_filter,unclassified_filter=unclassified_filter)
        pixel_hash = sha(image.width.to_bytes(4, 'little') + image.height.to_bytes(4, 'little') + image.tobytes())
        key = sha(json.dumps(dict(pixels=pixel_hash, policy=policy), sort_keys=True).encode())
        prepared.append((tid, path, raw, image, pixel_hash, key, policy))
    run = private / 'texture-runs' / name
    cache = private / 'texture-cache'
    for directory in [run.parent, cache]:
        if directory.is_symlink() or not directory.resolve().is_relative_to(private):
            raise ValueError('Texture output escapes private storage')
        directory.mkdir(parents=True, exist_ok=True)
    run.mkdir(exist_ok=False)
    report = dict(schema=1, local_only=True, status='failed', title_id=profile['id'],
                  executable_sha256=identity, manifest_sha256=sha(spec_bytes),
                  algorithm=ALGORITHM, pillow_version=pillow_version, textures=[],
                  runtime_integration='pending', review_required=True)
    try:
        for tid, path, raw, image, pixels, key, policy in prepared:
            target = cache / key
            if target.is_symlink():
                raise ValueError('Texture cache symlink rejected')
            hit = target.exists()
            if hit:
                metadata = json.loads(local_file(target, 'entry.json').read_text())
                if metadata['key'] != key or metadata['policy'] != policy or metadata['pixels'] != pixels:
                    raise ValueError('Texture cache metadata mismatch')
                expected = []; w, h = image.width * policy['scale'], image.height * policy['scale']
                while True:
                    expected.append((w,h))
                    if not policy['mipmaps'] or (w,h)==(1,1): break
                    w,h=max(1,w//2),max(1,h//2)
                if [(r['width'],r['height']) for r in metadata['levels']] != expected or [r['file'] for r in metadata['levels']] != [f'mip-{i:02d}.png' for i in range(len(expected))]:
                    raise ValueError('Texture cache level layout mismatch')
                for record in metadata['levels']:
                    if digest(local_file(target, record['file'])) != record['sha256']:
                        raise ValueError('Texture cache corruption')
            else:
                required=image.width*image.height*policy['scale']**2*6+1024*1024
                if shutil.disk_usage(private).free < min_free+required:
                    raise ValueError('Texture processing stopped to preserve free-space reserve')
                target.mkdir(exist_ok=False)
                levels = []
                from PIL import Image
                if policy['detail']=='adaptive':
                    image=refine_detail(image,policy['wrap'])
                result = image if policy['scale'] == 1 else image.resize((image.width * policy['scale'],image.height * policy['scale']),Image.Resampling.NEAREST) if policy['ui_filter']=='nearest' else resize(image,
                    (image.width * policy['scale'], image.height * policy['scale']), policy['wrap'], policy['alpha'])
                if policy['detail']=='postblend':
                    result=postblend_detail(result,policy['wrap'],policy['scale'])
                while True:
                    data = encode(result); filename = f'mip-{len(levels):02d}.png'
                    (target / filename).write_bytes(data)
                    levels.append(dict(file=filename, sha256=sha(data), width=result.width, height=result.height))
                    if not policy['mipmaps'] or result.size == (1, 1):
                        break
                    result = resize(result, (max(1, result.width // 2), max(1, result.height // 2)), policy['wrap'], policy['alpha'])
                metadata = dict(schema=1, key=key, pixels=pixels, policy=policy, levels=levels)
                save(target / 'entry.json', metadata)
            if path.read_bytes() != raw:
                raise ValueError('Source changed during texture processing')
            report['textures'].append(dict(id=tid, source=str(path.relative_to(private)),
                source_sha256=sha(raw), pixel_sha256=pixels, cache_key=key, cache_hit=hit,
                policy=policy, levels=metadata['levels'],
                review='classification_required' if policy['role'] == 'unclassified' else
                       'ui_scaling_review_required' if policy['role'] in {'ui','text'} else 'visual_review_required'))
        report['status'] = 'processed'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        save(run / 'report.json', report)
    return report
