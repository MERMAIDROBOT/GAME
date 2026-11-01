"""Private, explicit missing-target feedback. Candidates are not verified functions.

Design informed by snuri00/ps2-web-recomp c628ae8's record_miss/port.sh loop.
This module is an original implementation; no upstream source is copied.
"""
from pathlib import Path
import hashlib
import json
import re
import struct
import tomllib

MAX_LOG = 8 << 20
MAX_TARGETS = 4096
NATIVE = re.compile(r'^.*?\[guest-branch:missing-target\] [^\n]*?\btarget=0x([0-9a-fA-F]{1,8})(?=\s|$)', re.M)
WEB = re.compile(r'^\[ee\] no translated code at 0x([0-9a-fA-F]{8})(?=\s|$)', re.M)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def private_file(private, relative, limit=None):
    from pipeline import safe_relative
    private = Path(private).resolve()
    path = private / safe_relative(relative)
    # A resolved path is not enough: reject symlinked parents as well.
    if any(p.is_symlink() for p in [path, *path.parents] if p != private and p.is_relative_to(private)):
        raise ValueError('Feedback input contains a symlink')
    if not path.resolve().is_relative_to(private) or not path.is_file():
        raise ValueError('Feedback input must be a private regular file')
    if limit is not None and path.stat().st_size > limit:
        raise ValueError('Feedback input exceeds size budget')
    return path


def executable_spans(raw):
    """Only file-backed PF_X PT_LOAD bytes; no BSS, data, aliases or overlays."""
    if len(raw) < 52 or raw[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected ELF32 little-endian executable')
    h = struct.unpack_from('<HHIIIIIHHHHHH', raw, 16)
    if h[0] != 2 or h[1] != 8:
        raise ValueError('Expected executable MIPS ELF')
    off, size, count = h[4], h[8], h[9]
    if not count or count > 1024 or size < 32 or off + size * count > len(raw):
        raise ValueError('Invalid ELF program-header table')
    spans = []
    for i in range(count):
        kind, pos, addr, _, filesz, memsz, flags, _ = struct.unpack_from('<8I', raw, off + size * i)
        if kind != 1:
            continue
        if filesz > memsz or pos + filesz > len(raw) or addr + memsz > 1 << 32:
            raise ValueError('Invalid ELF load segment')
        if flags & 1 and filesz:
            spans.append((addr, addr + filesz, pos))
    if not spans:
        raise ValueError('ELF has no file-backed executable segments')
    for a, b, _ in spans:
        if any(a < d and c < b for c, d, _ in spans if (c, d) != (a, b)):
            raise ValueError('Overlapping executable load segments')
    if len({(a,b) for a,b,_ in spans}) != len(spans):
        raise ValueError('Duplicate executable load segments')
    return spans


def candidates(raw, text):
    spans = executable_spans(raw)
    addresses = sorted({int(m.group(1), 16) for pattern in (NATIVE, WEB) for m in pattern.finditer(text)})
    if len(addresses) > MAX_TARGETS:
        raise ValueError('Too many feedback targets')
    accepted, rejected = [], []
    for at in addresses:
        reason = 'misaligned' if at & 3 else None
        match = next(((a,p) for a,b,p in spans if a <= at and at + 4 <= b), None)
        if not reason and match is None:
            reason = 'not_in_main_elf_executable_file_bytes'
        if reason:
            rejected.append(dict(address=f'{at:08x}', reason=reason))
        else:
            a, p = match
            accepted.append(dict(address=f'{at:08x}', file_word=raw[p+at-a:p+at-a+4].hex()))
    return accepted, rejected


def config_with_hints(text, hints):
    before = tomllib.loads(text)
    if not isinstance(before.get('general'), dict):
        raise ValueError('Analyzer config has no general table')
    existing = before['general'].get('entry_points', [])
    if not isinstance(existing, list) or any(not isinstance(v, str) for v in existing):
        raise ValueError('Invalid existing entry_points')
    merged = sorted(set(existing + hints))
    # Replace only a standalone general.entry_points array. Parse afterwards to
    # prove every other configuration value stayed identical.
    header = re.search(r'^\[general\][ \t]*(?:#[^\n]*)?$', text, re.M)
    if not header:
        raise ValueError('Unsupported general table layout')
    end = re.search(r'^\[', text[header.end():], re.M)
    finish = header.end() + end.start() if end else len(text)
    section = text[header.end():finish]
    if 'entry_points' in before['general']:
        pattern = re.compile(r'^entry_points[ \t]*=[ \t]*\[(?:[^\]"\']|"(?:\\.|[^"\\])*"|\'(?:[^\'])*\')*\][ \t]*(?:#[^\n]*)?$', re.M)
        section, count = pattern.subn('entry_points = ' + json.dumps(merged), section)
        if count != 1:
            raise ValueError('Unsupported entry_points layout')
    else:
        section = '\nentry_points = ' + json.dumps(merged) + section
    result = text[:header.end()] + section + text[finish:]
    expected = dict(before)
    expected['general'] = dict(before['general'], entry_points=merged)
    if tomllib.loads(result) != expected:
        raise ValueError('Feedback changed unrelated analyzer settings')
    return result


def prepare(profile, private, log_relative, name):
    import pipeline as lab
    from compiler import identifier
    identifier(name)
    private = Path(private).resolve()
    log = private_file(private, log_relative, MAX_LOG)
    config = private_file(private, 'analysis/recomp.toml', 8 << 20)
    elf = lab.boot_executable(private/'disc-root', profile)
    if elf.is_symlink() or not elf.resolve().is_relative_to(private):
        raise ValueError('Unsafe executable')
    raw, log_raw, config_raw = elf.read_bytes(), log.read_bytes(), config.read_bytes()
    identity = digest(raw)
    if profile.get('expected_sha256') and identity != profile['expected_sha256']:
        raise ValueError('Executable revision differs from profile')
    settings = tomllib.loads(config_raw.decode())
    if Path(settings.get('general', {}).get('input', '')).resolve() != elf.resolve():
        raise ValueError('Analyzer config targets a different executable')
    accepted, rejected = candidates(raw, log_raw.decode(errors='replace'))
    hints = [f"feedback_{r['address']}@0x{r['address']}" for r in accepted]
    rendered = config_with_hints(config_raw.decode(), hints)
    report = dict(schema=1, implementation_sha256=digest(Path(__file__).read_bytes()), name=name, title_id=profile['id'], executable_sha256=identity,
        toolchain_lock_sha256=lab.digest(lab.ROOT/'toolchain.lock.json'),
        log=str(log.relative_to(private)), log_sha256=digest(log_raw),
        analyzer_sha256=digest(config_raw), candidate_config_sha256=digest(rendered.encode()),
        candidates=accepted, rejected=rejected, status='candidates_ready' if accepted else 'no_eligible_targets',
        compiler_hints=hints, local_only=True, runtime_code_identity_verified=False,
        module_generation_verified=False, functions_verified=False, playable_certified=False,
        scope='Main-ELF static compilation candidates only. Log provenance is recorded, not authenticated. '
              'Runtime overlays/self-modification and route completion are not qualified.')
    out = private/'analysis/runtime-feedback'/name
    if not out.resolve().is_relative_to(private) or any(p.is_symlink() for p in [out, *out.parents] if p != private and p.is_relative_to(private)):
        raise ValueError('Unsafe feedback output')
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    (out/'recomp.toml').write_text(rendered)
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def consume(profile, private, name, *, allow_empty=False):
    """Revalidate all inputs and regenerate candidates before translating."""
    import pipeline as lab
    from compiler import identifier
    identifier(name)
    report_path = private_file(private, f'analysis/runtime-feedback/{name}/report.json', 4 << 20)
    config_path = private_file(private, f'analysis/runtime-feedback/{name}/recomp.toml', 8 << 20)
    r = json.loads(report_path.read_text())
    if r.get('implementation_sha256') != digest(Path(__file__).read_bytes()):
        raise ValueError('Feedback implementation changed; collect a new run')
    if r.get('schema') != 1 or r.get('name') != name or r.get('title_id') != profile['id']:
        raise ValueError('Feedback report identity differs')
    base = private_file(private, 'analysis/recomp.toml', 8 << 20)
    log = private_file(private, r['log'], MAX_LOG)
    elf = lab.boot_executable(Path(private)/'disc-root', profile)
    if elf.is_symlink() or not elf.resolve().is_relative_to(Path(private).resolve()):
        raise ValueError('Unsafe executable')
    if profile.get('expected_sha256') and lab.digest(elf) != profile['expected_sha256']:
        raise ValueError('Executable revision differs from profile')
    for path, key in [(base,'analyzer_sha256'),(log,'log_sha256'),(elf,'executable_sha256'),
                      (lab.ROOT/'toolchain.lock.json','toolchain_lock_sha256'),(config_path,'candidate_config_sha256')]:
        if lab.digest(path) != r.get(key):
            raise ValueError('Feedback input drift: '+key)
    accepted, rejected = candidates(elf.read_bytes(), log.read_text(errors='replace'))
    hints = [f"feedback_{x['address']}@0x{x['address']}" for x in accepted]
    expected = config_with_hints(base.read_text(), hints)
    if (not accepted and not allow_empty) or accepted != r['candidates'] or rejected != r['rejected'] or hints != r['compiler_hints']:
        raise ValueError('Feedback candidates missing or changed')
    if config_path.read_text() != expected:
        raise ValueError('Feedback configuration changed')
    return config_path, dict(name=name, report_sha256=lab.digest(report_path),
        candidate_config_sha256=lab.digest(config_path), candidates=len(accepted),
        runtime_verified=False)


def plan_boot_recovery(profile,private,probe_relative):
    """Prepare bounded compilation candidates from an artifact-bound failed probe.

    Capturing a candidate is automatic. Consuming it still requires the explicit
    translation command: a log alone cannot establish runtime overlay identity.
    """
    import pipeline as lab
    private=Path(private).resolve()
    stage_path=private_file(private,probe_relative,4<<20)
    stage_raw=stage_path.read_bytes();stage=json.loads(stage_raw)
    log_path=Path(stage.get('log',''))
    try:log_relative=str(log_path.relative_to(private))
    except ValueError:raise ValueError('Probe log is outside private storage') from None
    log=private_file(private,log_relative,MAX_LOG)
    log_raw=log.read_bytes()
    if digest(log_raw)!=stage.get('log_sha256'):raise ValueError('Probe log changed')
    build_path=private_file(private,'analysis/native-build-stage.json',4<<20)
    build=json.loads(build_path.read_text())
    if lab.digest(build_path)!=stage.get('build_stage_sha256'):raise ValueError('Probe build receipt changed')
    binary=Path(build.get('binary',''))
    if not binary.is_absolute() or binary.is_symlink() or not binary.is_file() or lab.digest(binary)!=stage.get('binary_sha256') or build.get('sha256')!=stage.get('binary_sha256'):
        raise ValueError('Probe binary changed')
    elf=lab.boot_executable(private/'disc-root',profile)
    if lab.digest(elf)!=stage.get('executable_sha256'):raise ValueError('Probe executable changed')
    if lab.digest(lab.ROOT/'toolchain.lock.json')!=stage.get('toolchain_lock_sha256'):raise ValueError('Probe toolchain lock changed')
    from port_checks import audit
    observed=audit(log=log)
    if stage.get('probe_qualified') is not False or not any(f['code']=='guest_target_missing' for f in observed['findings']):
        raise ValueError('Probe has no failed missing-target observation')
    config=private_file(private,'analysis/recomp.toml',8<<20)
    key=dict(title=profile['id'],log=lab.digest(log),binary=stage['binary_sha256'],
             elf=stage['executable_sha256'],configuration=lab.digest(config),
             implementation=digest(Path(__file__).read_bytes()),lock=stage['toolchain_lock_sha256'])
    # A later boot overwrites its normal log. Preserve this probe before preparing
    # feedback, so its compilation hints do not silently lose their evidence.
    if lab.digest(stage_path)!=digest(stage_raw) or lab.digest(log)!=digest(log_raw):
        raise ValueError('Probe evidence changed while preparing recovery')
    evidence=private/'analysis/runtime-probe-evidence'/digest(stage_raw)
    if any(p.is_symlink() for p in [evidence,*evidence.parents] if p.is_relative_to(private)):
        raise ValueError('Unsafe probe evidence storage')
    evidence.mkdir(parents=True,mode=0o700,exist_ok=True)
    for name,raw in [('probe.json',stage_raw),('run.log',log_raw)]:
        target=evidence/name
        if target.is_symlink():raise ValueError('Unsafe probe evidence file')
        try:
            with target.open('xb') as stream:stream.write(raw)
            target.chmod(0o600)
        except FileExistsError:pass
        if not target.is_file() or target.read_bytes()!=raw:raise ValueError('Archived probe evidence changed')
    snapshot_relative=str((evidence/'run.log').relative_to(private))
    name='boot-'+digest(json.dumps(key,sort_keys=True).encode())[:24]
    directory=private/'analysis/runtime-feedback'/name
    if directory.exists() or directory.is_symlink():
        # consume rejects drift and unsafe paths; never overwrite old evidence.
        consume(profile,private,name,allow_empty=True)
        result=json.loads(private_file(private,str((directory/'report.json').relative_to(private)),4<<20).read_text())
        reused=True
    else:
        result=prepare(profile,private,snapshot_relative,name);reused=False
    return dict(schema=1,status='candidates_prepared' if result['candidates'] else 'no_safe_static_candidates',
        probe_sha256=digest(stage_raw),probe_evidence=str(evidence.relative_to(private)),feedback_name=name,
        feedback_report=str((directory/'report.json').relative_to(private)),
        feedback_report_sha256=lab.digest(directory/'report.json'),
        candidates=result['candidates'],rejected=result['rejected'],reused=reused,
        automatic_translation_allowed=False,runtime_code_identity_verified=False,
        gameplay_verified=False,
        next_action='Verify runtime code identity before explicitly consuming static main-ELF hints; overlays require module-specific recovery')
