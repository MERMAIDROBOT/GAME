"""Executable port qualification from actual artifacts, sources and runtime logs.

Checks do not launch a game or equate counters with visual/gameplay acceptance.
"""
from pathlib import Path
import hashlib
import json
import plistlib
import re


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inspect_app(app):
    app = Path(app).resolve()
    findings = []
    def flag(code, detail, severity='error'):
        findings.append(dict(code=code, severity=severity, detail=detail))
    path = app/'Contents/Resources/RunConfiguration.plist'
    if not path.is_file():
        return [dict(code='configuration_missing', severity='error', detail=str(path))]
    config = plistlib.loads(path.read_bytes())
    disc = Path(config.get('DiscDirectory', ''))
    executable = Path(config.get('Executable', ''))
    if not disc.is_absolute() or not disc.is_dir():
        flag('disc_missing', 'DiscDirectory must be an existing absolute directory')
    else:
        # CDVD's recursive discovery skips directory symlinks. Do not resolve
        # them away before checking: that hid the missing speech/music root.
        links = [str(disc)] if disc.is_symlink() else []
        links += [str(p) for p in disc.rglob('*') if p.is_symlink() and p.is_dir()]
        if links:
            flag('streaming_directory_symlinks', links)
    if not executable.is_absolute() or not executable.is_file():
        flag('executable_missing', 'Executable must be an existing absolute file')
    env = config.get('Environment', {})
    for key in ('PS2_PORT_HEADLESS', 'PS2_PORT_PRIVATE_WINDOW_CONTROL', 'PS2_PORT_FRAME',
                'PS2_PORT_TEST_PAD_SCRIPT', 'PS2_PORT_DIAGNOSTIC_SECONDS'):
        if str(env.get(key, '')).lower() not in ('', '0', 'false', 'off'):
            flag('diagnostic_release_setting', key)
    pack = env.get('PS2_PORT_TEX_PACK')
    if pack and not Path(pack).is_dir():
        flag('texture_pack_missing', str(pack))
    library = app/'Contents/Frameworks/libclaude-hwgs.dylib'
    # Hardware-GS uses this separate dylib; a plugin-only receipt misses it.
    if not library.is_file():
        flag('renderer_not_inventoried', str(library), 'warning')
    return findings


def audit(*, app=None, receipt=None, log=None, generated=None, acceptance=None, gif=None):
    findings = []
    inputs = {}
    if app:
        findings += inspect_app(app)
        config = Path(app)/'Contents/Resources/RunConfiguration.plist'
        if config.is_file(): inputs['configuration'] = sha(config)
    if receipt:
        if not app: raise ValueError('Artifact receipt requires --app')
        data = json.loads(Path(receipt).read_text())
        inputs['receipt'] = sha(receipt)
        root = Path(app).resolve()/'Contents'
        files = data.get('files', {})
        if not isinstance(files, dict) or not files:
            raise ValueError('Receipt must contain nonempty files mapping relative to Contents')
        for rel, expected in files.items():
            path = root/rel
            if not path.resolve().is_relative_to(root) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Unsafe artifact receipt entry')
            if not path.is_file() or sha(path) != expected:
                findings.append(dict(code='artifact_mismatch', severity='error', detail=rel))
        if (root/'Frameworks/libclaude-hwgs.dylib').is_file() and 'Frameworks/libclaude-hwgs.dylib' not in files:
            findings.append(dict(code='renderer_receipt_missing', severity='error', detail='Hardware renderer must be pinned separately from plugin'))
    if generated:
        from branches import rewrite
        root = Path(generated).resolve()
        if not root.is_dir(): raise ValueError('Generated source directory does not exist')
        for path in sorted(root.rglob('*.cpp')):
            if path.is_symlink(): raise ValueError('Generated source symlinks are not audited')
            try:
                _, count, changes = rewrite(path.read_text())
                if changes:
                    findings.append(dict(code='ee_branch_width', severity='error', file=str(path.relative_to(root)), instructions=changes))
            except ValueError as error:
                findings.append(dict(code='ee_branch_shape_unknown', severity='error', file=str(path.relative_to(root)), detail=str(error)))
    packet_report=None
    if gif:
        from gif_packets import inspect
        path=Path(gif)
        if path.stat().st_size>128*1024*1024: raise ValueError('GIF stream exceeds 128 MiB')
        inputs['gif']=sha(path)
        packet_report=inspect(path.read_bytes())
    metrics = {}
    if log:
        path = Path(log)
        if path.stat().st_size > 128 * 1024 * 1024: raise ValueError('Log exceeds 128 MiB limit')
        text = path.read_text(errors='replace'); inputs['log'] = sha(path)
        for label, pattern in {
            'audio_missing_frames': r'(\d+) missing frames',
            'audio_underruns': r'(\d+) underruns',
            'audio_dropped_frames': r'(\d+) dropped frames',
            'texture_load_failures': r'load_failures=(\d+)',
            'field_peak_ms': r'peak_ms=([\d.]+)',
        }.items():
            values = [float(v) for v in re.findall(pattern, text)]
            if values:
                metrics[label] = max(values)
                if metrics[label] > (33.34 if label == 'field_peak_ms' else 0):
                    findings.append(dict(code=label, severity='warning' if label == 'field_peak_ms' else 'error', maximum=metrics[label]))
        hits = re.findall(r'\[residency-cache\] hits=(\d+) misses=(\d+)', text)
        if hits:
            h, m = sum(int(a) for a,b in hits), sum(int(b) for a,b in hits)
            metrics['residency_query_avoidance'] = h/(h+m) if h+m else None
    # Human/visual acceptance remains unknown unless evidence names this exact
    # configuration AND all executable/renderer artifacts in the supplied receipt.
    accepted_scenarios = []
    if acceptance:
        if not app or not receipt: raise ValueError('Acceptance needs app and pinned receipt')
        data = json.loads(Path(acceptance).read_text())
        pin = sha(receipt)
        if data.get('receipt_sha256') != pin: raise ValueError('Acceptance targets a different artifact receipt')
        for item in data.get('scenarios', []):
            if not isinstance(item, dict) or item.get('status') not in ('passed','failed','unverified'):
                raise ValueError('Invalid scenario acceptance')
            evidence = Path(item.get('evidence', ''))
            if not evidence.is_absolute(): evidence = Path(acceptance).resolve().parent/evidence
            if item['status']=='passed':
                if not evidence.is_file() or sha(evidence) != item.get('evidence_sha256'):
                    raise ValueError('Scenario evidence missing or changed')
                accepted_scenarios.append(item['id'])
            else:
                findings.append(dict(code='scenario_'+item['status'], severity='error', detail=item.get('id')))
    return dict(schema=1, findings=findings, metrics=metrics, inputs=inputs,
                structural_checks_passed=not any(f['severity']=='error' for f in findings),
                accepted_scenarios=accepted_scenarios, gif=packet_report,
                gameplay_verified=False,
                coverage='Reported scenarios only; missing audio or timing counters remain unknown. Field timings and cache hits are not unique FPS.')
