"""Complete stage graph and evidence scheduler; missing executors cannot pass.

This schedules recorded evidence, not executable strings from data. The concrete
stage modules own execution; unsupported integration is explicit in the report.
"""
from pathlib import Path
import json
import pipeline as lab


def load(path=None):
    spec=json.loads(Path(path or lab.ROOT/'enhancements.json').read_text())
    if spec.get('schema')!=1: raise ValueError('Unknown enhancement graph schema')
    ids=set()
    for stage in spec['stages']:
        if stage['id'] in ids: raise ValueError('Duplicate stage')
        if any(parent not in ids for parent in stage['requires']): raise ValueError('Stage graph is not topologically ordered')
        lab.safe_relative(stage['artifact']);ids.add(stage['id'])
    return spec


def schedule(profile, private):
    spec=load();private=Path(private).resolve()
    identity=lab.digest(lab.boot_executable(private/'disc-root',profile))
    rows=[]; completed=set()
    # A single boot/config/pack file is never sufficient evidence for another stage.
    for stage in spec['stages']:
        path=(private/'engine-evidence/receipts'/(stage['id']+'.json')).resolve()
        if not path.is_relative_to(private): raise ValueError('Evidence escapes private root')
        row=dict(stage, status='blocked_dependencies',missing_dependencies=[p for p in stage['requires'] if p not in completed])
        if not row['missing_dependencies']:
            row['status']='ready_for_configured_executor' if stage['executor'] else 'implementation_required'
        if path.is_file() and not row['missing_dependencies']:
            evidence=json.loads(path.read_text())
            # Only explicit per-stage, per-revision records qualify. Historical
            # reports remain useful references but cannot certify a future build.
            valid=(evidence.get('stage')==stage['id'] and evidence.get('executable_sha256')==identity
                   and evidence.get('status')=='passed' and evidence.get('inputs')
                   and evidence.get('graph_sha256')==lab.digest(lab.ROOT/'enhancements.json'))
            if valid:
                for item in evidence['inputs']:
                    f=(private/lab.safe_relative(item['path'])).resolve()
                    if not f.is_relative_to(private) or not f.is_file() or lab.digest(f)!=item['sha256']:
                        valid=False;break
            if valid:
                row['status']='passed';row['evidence_sha256']=lab.digest(path);completed.add(stage['id'])
            else: row['unqualified_evidence']=str(path)
        rows.append(row)
    report=dict(schema=1,local_only=True,executable_sha256=identity,
        graph_sha256=lab.digest(lab.ROOT/'enhancements.json'), policy=spec['policy'], stages=rows,
        completed=len(completed), total=len(rows), all_steps_qualified=len(completed)==len(rows),
        scope='Evidence-backed full-process scheduler. Executor labels are module bindings, not proof of automatic end-to-end integration.')
    lab.save(private/'analysis/enhancement-engine-plan.json',report)
    return report


def record_stage(profile, private, stage_id, artifact):
    """Called by concrete executors after success; pin output and predecessor receipts."""
    private=Path(private).resolve();spec=load()
    stage=next(s for s in spec['stages'] if s['id']==stage_id)
    inputs=[dict(path=str(Path(artifact)),sha256=lab.digest(private/artifact))]
    if stage_id=='disc-integrity' and (private/'disc-manifest.json').is_file():
        inputs.append(dict(path='disc-manifest.json',sha256=lab.digest(private/'disc-manifest.json')))
        for item in json.loads((private/'disc-manifest.json').read_text())['files']:
            rel=Path('disc-root')/lab.safe_relative(item['path'])
            inputs.append(dict(path=str(rel),sha256=item['sha256']))
    for parent in stage['requires']:
        receipt=Path('engine-evidence/receipts')/(parent+'.json')
        if not (private/receipt).is_file(): return # Cannot certify an incomplete chain.
        inputs.append(dict(path=str(receipt),sha256=lab.digest(private/receipt)))
    data=dict(stage=stage_id,executable_sha256=lab.digest(lab.boot_executable(private/'disc-root',profile)),
              status='passed',graph_sha256=lab.digest(lab.ROOT/'enhancements.json'),inputs=inputs)
    lab.save(private/'engine-evidence/receipts'/(stage_id+'.json'),data)
