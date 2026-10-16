"""Pinned local PGSCAP replay. Captures and reports stay in private game storage."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

import rawgs
from pipeline import WORK, digest, save


def private_file(private, value):
    path = (private / value).resolve()
    if not path.is_relative_to(private) or not path.is_file():
        raise ValueError('Replay inputs must be files inside private game storage')
    return path


def pinned_file(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get('path'), str) or not isinstance(spec.get('sha256'), str):
        raise ValueError('Backend pin requires a local path and SHA256')
    path = Path(spec['path'])
    if not path.is_absolute() or not path.is_file():
        raise ValueError('Backend files require absolute local paths')
    if not re.fullmatch(r'[0-9a-f]{64}', spec['sha256']) or digest(path) != spec['sha256']:
        raise ValueError('Replay backend pin mismatch')
    return path.resolve()


def run_replay(profile, private, capture, backend, name, batch='on', timeout=300):
    private = Path(private).resolve()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', name):
        raise ValueError('Invalid replay run name')
    if batch not in ('off', 'on') or not 1 <= timeout <= 1800:
        raise ValueError('Invalid replay treatment or timeout')
    capture = private_file(private, capture)
    backend = private_file(private, backend)
    spec = json.loads(backend.read_text())
    if not isinstance(spec, dict) or spec.get('schema') != 1 or spec.get('protocol') != 'pgscap-cli-v1':
        raise ValueError('Unsupported local replay backend protocol')
    executable = pinned_file(spec.get('executable'))
    loader = pinned_file(spec.get('moltenvk'))
    if not os.access(executable, os.X_OK):
        raise ValueError('Replay backend is not executable')
    # Validate before starting a GPU process, including unsupported ingress records.
    validation = rawgs.validate(capture)
    if not validation['replayable']:
        raise ValueError('Capture contains unqualified or incomplete ingress')
    capture_hash = digest(capture)
    output = private / 'logs/replays' / name
    # Advisory lock covers this replay command across title profiles. Other tools
    # still require an explicit process audit; this is not a global GPU scheduler.
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / '.raw-gs-replay.lock').open('a') as lock:
        try:
            # Validation can take time on multi-GiB captures. Recheck the pins
            # immediately before execution rather than trusting earlier reads.
            pinned_file(spec['executable'])
            pinned_file(spec['moltenvk'])
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another pipeline graphics replay is active') from None
        output.mkdir(parents=True, exist_ok=False)
        report = {'status': 'running', 'local_only': True, 'profile_id': profile['id'],
                  'capture_sha256': capture_hash, 'capture_validation': validation,
                  'backend_manifest_sha256': digest(backend), 'backend': spec,
                  'batch': batch, 'timeout_seconds': timeout,
                  'scope': 'Terminal VRAM differential only. No scanout history, original-hardware fidelity, gameplay or FPS acceptance.'}
        save(output / 'report.json', report)
        env = {k: v for k, v in os.environ.items() if not k.startswith('PS2_PORT_PRIVATE_')}
        env['PS2_PORT_PRIVATE_GIF_BATCH'] = '1' if batch == 'on' else '0'
        started = time.monotonic()
        try:
            with (output / 'process.log').open('w') as log:
                result = subprocess.run([str(executable), '--capture', str(capture),
                                         '--output', str(output), '--moltenvk', str(loader)],
                                        env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=timeout)
            report['returncode'] = result.returncode
            if result.returncode:
                raise RuntimeError('Replay process failed; inspect private process.log')
            native = json.loads((output / 'result.json').read_text())
            snapshot = output / 'vram.bin'
            if not isinstance(native, dict) or native.get('stage') != 'complete' or native.get('status') not in ('passed', 'pixel_mismatch'):
                raise ValueError('Replay has no completed result')
            if native.get('records_replayed') != validation['record_count'] or native.get('compared_vram_bytes') != rawgs.VRAM_SIZE:
                raise ValueError('Replay coverage does not match capture')
            if snapshot.stat().st_size != rawgs.VRAM_SIZE:
                raise ValueError('Replay snapshot is not complete VRAM')
            actual = digest(snapshot)
            matches = actual == validation['final_vram_sha256']
            mismatches = native.get('mismatched_bytes')
            if type(mismatches) is not int or not 0 <= mismatches <= rawgs.VRAM_SIZE:
                raise ValueError('Invalid differential byte count')
            if matches != (mismatches == 0) or matches != (native['status'] == 'passed'):
                raise ValueError('Replay result contradicts saved snapshot')
            # A completed mismatch is an observation, never an accepted rendering fix.
            report.update(status='observed', native_result=native, final_vram_sha256=actual,
                          matches_recorded_footer=matches, original_fidelity_verified=False,
                          gameplay_verified=False)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            report.update(status='failed', error=str(error))
        finally:
            report['elapsed_seconds'] = time.monotonic() - started
            save(output / 'report.json', report)
        return report
