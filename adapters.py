"""Stage explicitly revision-gated title adapters into local copies only."""
from pathlib import Path
import hashlib
import json
import re

MAX_INPUT_BYTES = 64*1024*1024


def hexadecimal(value):
    if not isinstance(value,str) or not value or len(value)%2 or not re.fullmatch(r'[a-f0-9]+',value):
        raise ValueError('Adapter bytes must be nonempty lowercase hexadecimal')
    return bytes.fromhex(value)


def guarded_bytes(raw, guards, edits):
    """Validate every guard/edit first, then return an independent patched copy."""
    prepared = []
    for item in guards:
        offset = item['offset']
        before = hexadecimal(item['before'])
        if type(offset) is not int or offset < 0 or offset+len(before)>len(raw):
            raise ValueError('Adapter guard is outside input')
        if raw[offset:offset+len(before)] != before:
            raise ValueError('Adapter guard does not match')
    for item in edits:
        offset = item['offset']
        before,after = hexadecimal(item['before']),hexadecimal(item['after'])
        if len(before)!=len(after) or before==after:
            raise ValueError('Adapter edit must change bytes without changing size')
        if type(offset) is not int or offset < 0 or offset+len(before)>len(raw):
            raise ValueError('Adapter edit is outside input')
        if raw[offset:offset+len(before)]!=before:
            raise ValueError('Adapter original bytes do not match')
        prepared.append((offset,before,after))
    if not prepared:
        raise ValueError('Adapter needs edits')
    intervals = sorted((at,at+len(before)) for at,before,_ in prepared)
    if any(a[1]>b[0] for a,b in zip(intervals,intervals[1:])):
        raise ValueError('Adapter edits overlap')
    result = bytearray(raw)
    for offset,before,after in prepared:
        result[offset:offset+len(before)] = after
    return bytes(result)


def stage_adapters(profile,private,manifest,name):
    from compiler import identifier, sha
    from pipeline import boot_executable, digest, safe_relative, save
    identifier(name)
    private=Path(private).resolve()
    manifest=(private/manifest).resolve()
    if not manifest.is_relative_to(private) or not manifest.is_file():
        raise ValueError('Adapter manifest must live inside private game storage')
    encoded=manifest.read_bytes()
    spec=json.loads(encoded)
    if spec.get('schema')!=1 or spec.get('title_id')!=profile['id']:
        raise ValueError('Adapter manifest schema/title mismatch')
    disc=(private/'disc-root').resolve()
    identity=digest(boot_executable(disc,profile))
    if identity!=sha(spec['executable_sha256']) or (profile.get('expected_sha256') and identity!=profile['expected_sha256']):
        raise ValueError('Adapter targets a different executable revision')
    target=private/'adapter-runs'/name
    if not target.resolve().is_relative_to(private):
        raise ValueError('Adapter output escapes private storage')
    target.mkdir(parents=True,exist_ok=False)
    report=dict(local_only=True,status='failed',manifest_sha256=hashlib.sha256(encoded).hexdigest(),
        executable_sha256=identity,adapters=[],
        scope='Guarded local copies only; not activated in game loading or packaging. '
              'No guest execution or gameplay qualification.')
    try:
        pending=[]
        seen=set()
        ids=set()
        if not spec.get('adapters'):
            raise ValueError('Manifest needs adapters')
        for item in spec['adapters']:
            identifier(item['id'])
            if item['id'] in ids:
                raise ValueError('Duplicate title adapter identifier')
            ids.add(item['id'])
            rel=safe_relative(item['file'])
            source=(disc/rel).resolve()
            if not source.is_relative_to(disc) or not source.is_file() or (disc/rel).is_symlink():
                raise ValueError('Unsafe adapter input')
            if str(rel) in seen:
                raise ValueError('Multiple adapters for one file require one combined manifest entry')
            seen.add(str(rel))
            if source.stat().st_size>MAX_INPUT_BYTES:
                raise ValueError('Adapter input exceeds bounded module size')
            if not item.get('reason') or not item.get('removal_condition'):
                raise ValueError('Title adapter needs justification and removal condition')
            with source.open('rb') as stream:
                raw=stream.read(MAX_INPUT_BYTES+1)
            if len(raw)>MAX_INPUT_BYTES:
                raise ValueError('Adapter input grew beyond bounded module size')
            before=hashlib.sha256(raw).hexdigest()
            if before!=sha(item['input_sha256']):
                raise ValueError('Adapter input revision differs')
            patched=guarded_bytes(raw,item.get('guards',[]),item['edits'])
            pending.append((rel,patched))
            report['adapters'].append(dict(id=item['id'],file=str(rel),input_sha256=before,
                output_sha256=hashlib.sha256(patched).hexdigest(),edits=len(item['edits']),
                guards=len(item.get('guards',[])),reason=item['reason'],
                removal_condition=item['removal_condition']))
        # No patched file is emitted unless all input revisions/guards pass.
        for rel,patched in pending:
            destination=target/'files'/rel
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(patched)
        report['status']='staged'
    except Exception as error:
        report['error']=str(error)
        save(target/'report.json',report)
        raise
    save(target/'report.json',report)
    return report
