"""Compile the GPU renderer's GLSL to SPIR-V headers in runtime/gpu/shaders/spirv/, committed so a normal build needs
no glslang. Mirrors the shader list in runtime/gpu/CMakeLists.txt. inputs.json records the sha256 of every input
and the compiler version; --check (no glslang needed) fails when a shader changed without regenerating.

Usage: python3 tools/gen_shaders.py --glslang PATH      regenerate
       python3 tools/gen_shaders.py --check             verify the committed headers match their inputs"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GPU = ROOT / 'runtime/gpu'
SHADERS = GPU / 'shaders'
OUT = SHADERS / 'spirv'
INCLUDES = [SHADERS / 'gs_address.glsl', SHADERS / 'smaa_common.glsl', GPU / 'third_party/smaa/SMAA.glsl']
PLAIN = ['gs.vert', 'gs.frag', 'date.vert', 'date.frag', 'swizzle.comp', 'decode.comp', 'depth_load.frag', 'patch.comp',
         'fxaa.comp', 'downsample.comp', 'smaa_edges.comp', 'smaa_weights.comp', 'smaa_blend.comp']
VARIANTS = [('gs_frag_single', 'gs.frag', 'SINGLE_OUTPUT'), ('swizzle_image_comp', 'swizzle.comp', 'IMAGE'),
            ('decode_image_comp', 'decode.comp', 'TO_IMAGE'), ('swizzle_depth_comp', 'swizzle.comp', 'DEPTH_IMAGE'),
            ('gs_frag_blend', 'gs.frag', 'SHADER_BLEND')]


def jobs():
    for shader in PLAIN:
        yield shader.replace('.', '_'), shader, None
    yield from VARIANTS


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted({SHADERS / s for _, s, _ in jobs()} | set(INCLUDES))}


def generate(glslang):
    OUT.mkdir(exist_ok=True)
    version = subprocess.run([glslang, '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    outputs = {}
    for name, shader, define in jobs():
        command = [glslang, '-V', '--target-env', 'vulkan1.3', '--vn', f'spirv_{name}', f'-I{GPU / "third_party/smaa"}']
        if define: command.append(f'-D{define}')
        command += ['-o', str(OUT / f'{name}.h'), str(SHADERS / shader)]
        subprocess.run(command, check=True, capture_output=True)
        outputs[f'{name}.h'] = sha(OUT / f'{name}.h')
    (OUT / 'inputs.json').write_text(json.dumps(dict(compiler=version, target='vulkan1.3', inputs=inputs(),
                                                     outputs=outputs), indent=1) + '\n')
    print(len(outputs), 'shader headers;', version)


def check():
    record = json.loads((OUT / 'inputs.json').read_text())
    problems = [f'{k} changed' for k, v in inputs().items() if record['inputs'].get(k) != v]
    problems += [f'{k} output changed' for k, v in record['outputs'].items() if not (OUT / k).exists() or sha(OUT / k) != v]
    problems += [f'{n}.h missing' for n, _, _ in jobs() if f'{n}.h' not in record['outputs']]
    if problems:
        sys.exit('stale SPIR-V headers (run tools/gen_shaders.py --glslang PATH): ' + '; '.join(problems))


if __name__ == '__main__':
    if '--check' in sys.argv:
        check()
    else:
        generate(sys.argv[sys.argv.index('--glslang') + 1])
