"""Rebase pinned title corrections onto a new compiler's output, without activating them.

An unchanged game revision is mandatory. A clean three-way merge is a source
candidate, not gameplay acceptance; conflicts never produce a replay manifest.
"""
from pathlib import Path
import gzip
import hashlib
import json
import re
import subprocess
from clean_build import path_in, sha

MAX_SOURCE = 16 << 20


def read_base(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(MAX_SOURCE + 1)
    else:
        raw = path.read_bytes() if path.stat().st_size <= MAX_SOURCE else bytes(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE:
        raise ValueError('Correction baseline exceeds source budget')
    return raw


def rebase(profile, private, previous_private, previous_spec, bases, destination):
    import pipeline as lab
    private, previous_private = Path(private).resolve(), Path(previous_private).resolve()
    if set(previous_spec) != {'path', 'sha256'}:
        raise ValueError('Invalid previous correction declaration')
    manifest = path_in(previous_private, previous_spec['path'])
    if sha(manifest) != previous_spec['sha256']:
        raise ValueError('Previous correction manifest pin changed')
    previous = json.loads(manifest.read_text())
    if previous.get('schema') != 1 or previous.get('kind') != 'revision_generated_corrections':
        raise ValueError('Unsupported previous correction manifest')
    translation = lab.require_accepted_translation(private)
    identity = sha(lab.boot_executable(private / 'disc-root', profile))
    if identity != profile.get('expected_sha256') or identity != previous['executable_sha256']:
        raise ValueError('Rebase requires the exact same original executable revision')
    out = path_in(private, destination)
    if out.exists():
        raise ValueError('Preserve the existing rebase candidate; choose a new destination')
    edits, headers = previous.get('edits'), previous.get('headers')
    if not isinstance(edits, list) or not 0 < len(edits) <= 256 or not isinstance(headers, list) or len(headers) > 64:
        raise ValueError('Invalid previous correction inventory')
    if bases is None:
        bases = {r['file']: 'analysis/generated-correction-baselines/' + r['before_sha256'] + '.cpp' for r in edits}
    if set(bases) != {r['file'] for r in edits}:
        raise ValueError('Every prior correction needs one pinned baseline')
    prepared, seen = [], set()
    for row, header in [(r, False) for r in edits] + [(r, True) for r in headers]:
        name = row['file']
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.' + ('(?:h|hpp)' if header else 'cpp'), name) or name in seen:
            raise ValueError('Invalid or duplicate correction filename')
        seen.add(name)
        after = path_in(previous_private, row['source'])
        if sha(after) != row['after_sha256'] or after.stat().st_size > MAX_SOURCE:
            raise ValueError('Previous corrected source pin changed')
        if header:
            prepared.append((row, True, None, None, after.read_bytes()))
            continue
        base = path_in(previous_private, bases[name])
        raw = read_base(base)
        if hashlib.sha256(raw).hexdigest() != row['before_sha256']:
            raise ValueError('Previous baseline pin changed: ' + name)
        fresh = path_in(private, 'generated/' + name)
        if not fresh.is_file() or fresh.stat().st_size > MAX_SOURCE:
            raise ValueError('Fresh compiler omitted or oversized corrected function: ' + name)
        if sha(fresh) != translation.get('generated_sources', {}).get(name):
            raise ValueError('Fresh source differs from accepted translation receipt: ' + name)
        prepared.append((row, False, raw, fresh.read_bytes(), after.read_bytes()))
    from generated_corrections import retain_baselines
    retain_baselines(previous_private, {hashlib.sha256(base).hexdigest(): base for _, header, base, _, _ in prepared if not header})
    retain_baselines(private, {hashlib.sha256(fresh).hexdigest(): fresh for _, header, _, fresh, _ in prepared if not header})
    out.mkdir(parents=True)
    new_edits, new_headers, results = [], [], []
    for row, header, base, fresh, after in prepared:
        name = row['file']
        if header:
            current = private / 'generated' / name
            if current.exists() and (current.is_symlink() or current.read_bytes() != after):
                raise ValueError('Fresh compiler header collides with private helper: ' + name)
            (out / name).write_bytes(after)
            new_headers.append(dict(row, source=str((out / name).relative_to(private))))
            continue
        work = out / 'merge-inputs' / name
        work.mkdir(parents=True)
        for label, raw in [('base.cpp', base), ('fresh.cpp', fresh), ('previous-corrected.cpp', after)]:
            (work / label).write_bytes(raw)
        merge = subprocess.run(['git', 'merge-file', '-p', '--diff3', str(work / 'fresh.cpp'),
                                str(work / 'base.cpp'), str(work / 'previous-corrected.cpp')], capture_output=True)
        if merge.returncode < 0 or merge.returncode >= 128:
            raise RuntimeError('Source merge failed: ' + merge.stderr.decode(errors='replace'))
        candidate = out / name
        candidate.write_bytes(merge.stdout)
        clean = merge.returncode == 0
        results.append(dict(file=name, clean=clean, before_sha256=hashlib.sha256(fresh).hexdigest(),
                            after_sha256=hashlib.sha256(merge.stdout).hexdigest(), old_before=row['before_sha256'],
                            old_after=row['after_sha256']))
        if clean:
            new_edits.append(dict(row, source=str(candidate.relative_to(private)),
                                  before_sha256=hashlib.sha256(fresh).hexdigest(),
                                  after_sha256=hashlib.sha256(merge.stdout).hexdigest()))
    conflicts = [r['file'] for r in results if not r['clean']]
    report = dict(schema=1, executable_sha256=identity, previous_manifest_sha256=previous_spec['sha256'],
                  status='conflicts' if conflicts else 'source_candidate', conflicts=conflicts, files=results,
                  activated=False, native_built=False, gameplay_verified=False)
    if not conflicts:
        replacement = dict(schema=1, kind='revision_generated_corrections', executable_sha256=identity,
                           edits=new_edits, headers=new_headers)
        target = out / 'manifest.json'
        target.write_text(json.dumps(replacement, indent=2) + '\n')
        report['candidate_declaration'] = dict(path=str(target.relative_to(private)), sha256=sha(target))
    (out / 'rebase-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
