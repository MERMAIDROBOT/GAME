"""Optional, byte-pinned reference boundaries for independently recovered modules.

Only metadata is read. Reference build scripts and C bodies are never executed
or substituted. The source image, every metadata file and the recovered section
layout must agree before a reference can influence native code generation.
"""
from pathlib import Path
import hashlib
import json
import re
import tomllib
from compute_slot import exclusive


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8<<20),b''):h.update(block)
    return h.hexdigest()


def metadata(record):
    path=Path(record['path'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>8<<20:
        raise ValueError('Reference metadata must be a bounded absolute regular file')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record.get('sha256'):
        raise ValueError('Reference metadata hash differs from its pin')
    return raw


def load(profile,private):
    recipe=profile.get('reference_functions')
    if recipe is None:return None
    if not isinstance(recipe,dict) or recipe.get('format')!='module-placements-tsv-v1':
        raise ValueError('Unknown reference function-map format')
    source=recipe['source'];relative=Path(source['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Reference source image must be inside private storage')
    path=Path(private)/relative
    if not path.is_file() or digest(path)!=source.get('sha256'):
        raise ValueError('Reference source image hash differs; refusing foreign function boundaries')
    functions={}
    for line in metadata(recipe['placements']).decode('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):continue
        fields=line.split('\t')
        if len(fields)<6:raise ValueError('Invalid reference placement row')
        size=int(fields[2])
        if size<=0 or size&3 or size>16<<20:raise ValueError('Invalid reference function size')
        for place in fields[5].split(','):
            level,address=place.split(':');index=int(level);address=int(address,16)
            if index<0 or index>4095 or address&3 or not 0x100000<=address<address+size<=32<<20:
                raise ValueError('Invalid reference function placement')
            entries=functions.setdefault(index,{})
            if address in entries and entries[address]!=size:raise ValueError('Conflicting reference function boundaries')
            entries[address]=size
            if len(entries)>65536:raise ValueError('Reference function count exceeds budget')
    modules=[];seen=set()
    for record in recipe['modules']:
        module=json.loads(metadata(record));index=module['index']
        if type(index) is not int or index in seen:raise ValueError('Duplicate or invalid reference module identity')
        seen.add(index)
        sections=[(s['addr'],s['size'],s['type']==8) for s in module['sections'] if s['flags']&2]
        modules.append(dict(index=index,entry=module['entry'],sections=sections,
                            metadata_sha256=record['sha256']))
    if not modules or set(functions)-seen:raise ValueError('Reference placements lack module metadata')
    pin=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
    return dict(recipe_sha256=pin,source_sha256=source['sha256'],modules=modules,functions=functions,
                placements_sha256=recipe['placements']['sha256'])


def apply(context,image,config,directory):
    if context is None:return None
    layout=image['layout']
    if not layout:return dict(matched=False,reason='No independently qualified section layout')
    sections=[(s['address'],s['bytes'],s['zero_fill']) for s in layout['segments']]
    matches=[m for m in context['modules'] if m['entry']==layout['entry'] and m['sections']==sections]
    if not matches:return dict(matched=False,reason='No reference for this independently recovered module')
    if len(matches)!=1:raise ValueError('Ambiguous reference module layout')
    module=matches[0];entries=context['functions'].get(module['index'],{})
    if not entries:raise ValueError('Matched reference module has no function placements')
    lines=['Name,Start,End,Size']
    for address,size in sorted(entries.items()):
        if not any(image['base']+a<=address and address+size<=image['base']+b for a,b in image['spans']):
            raise ValueError('Reference function extends outside verified executable bytes')
        lines.append(f'reference_{address:08x},0x{address:x},0x{address+size:x},{size}')
    path=Path(directory)/'reference-functions.csv';path.write_text('\n'.join(lines)+'\n')
    config=Path(config);text=config.read_text();before=tomllib.loads(text)
    if before['general'].get('ghidra_output'):
        raise ValueError('Reference import cannot overwrite an existing function map')
    replacement='ghidra_output = '+json.dumps(str(path.resolve()))
    updated,count=re.subn(r'^ghidra_output[ \t]*=[ \t]*"(?:\\.|[^"\\])*"[ \t]*(?:#[^\n]*)?$',
                          lambda _:replacement,text,flags=re.M)
    expected=dict(before);expected['general']=dict(before['general'],ghidra_output=str(path.resolve()))
    if count!=1 or tomllib.loads(updated)!=expected:raise ValueError('Unsupported reference function-map configuration')
    config.write_text(updated)
    return dict(matched=True,module=module['index'],functions=len(entries),
                recipe_sha256=context['recipe_sha256'],source_sha256=context['source_sha256'],
                metadata_sha256=module['metadata_sha256'],placements_sha256=context['placements_sha256'],
                map_sha256=digest(path),native_gameplay_verified=False)


def audit(context,image,directory):
    """Compare required entry points with the emitted native registration table."""
    if context is None:return dict(passed=True,checked=0)
    layout=image['layout']
    if not layout:return dict(passed=True,checked=0,unqualified_reference=True)
    sections=[(s['address'],s['bytes'],s['zero_fill']) for s in layout['segments']]
    matches=[m for m in context['modules'] if m['entry']==layout['entry'] and m['sections']==sections]
    if not matches:return dict(passed=True,checked=0,no_reference_for_module=True)
    if len(matches)!=1:raise ValueError('Ambiguous reference module layout')
    tables=list(Path(directory).glob('*register_functions.cpp'))
    if len(tables)!=1:raise ValueError('Reference entry audit requires one registration table')
    covered={int(a,16) for a in re.findall(r'\[[0-9]+u?\]\s*=\s*\w+\s*;\s*//\s*0x([0-9a-fA-F]+)',tables[0].read_text())}
    required=set(context['functions'][matches[0]['index']]);missing=sorted(required-covered)
    return dict(passed=not missing,module=matches[0]['index'],checked=len(required),missing=[f'{a:08x}' for a in missing],
                scope='Verified reference ENTRY coverage only; not semantic or gameplay completion')


@exclusive('reference-overlay-audit')
def audit_port(profile,private):
    import overlays
    import pipeline
    private=Path(private);context=load(profile,private)
    if context is None:raise ValueError('Profile has no pinned reference function map')
    regions=overlays.candidates(json.loads((private/'analysis/code-census.json').read_text()))[0]
    rows=[]
    for oid,region in regions:
        row=audit(context,overlays.program_image(private,region),private/'overlays'/oid/'generated')
        rows.append(dict(overlay=oid,**row))
    present={r['module'] for r in rows if r.get('checked')}
    missing=sorted({m['index'] for m in context['modules']}-present)
    report=dict(schema=1,recipe_sha256=context['recipe_sha256'],source_sha256=context['source_sha256'],
                passed=all(r['passed'] for r in rows) and not missing,overlays=rows,
                missing_reference_modules=missing,modules_without_reference=[r['overlay'] for r in rows if not r.get('checked')],
                required_entries=sum(r['checked'] for r in rows),
                missing_entries=sum(len(r.get('missing',[])) for r in rows),
                full_port_certified=False,scope='Required function entries in pinned reference modules only')
    pipeline.save(private/'analysis/reference-entry-audit.json',report)
    return report
