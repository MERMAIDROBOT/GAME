"""Executable, versioned compiler knowledge. Data selects audited handlers, never eval.

New verified fixes extend the bank with fixtures. Unsupported contracts become
explicit gaps rather than guessed patches. Private title rules require ELF hashes.
"""
from pathlib import Path
import hashlib
import json
import re
import struct
import pipeline as lab

KINDS = {'mmi_pmulth', 'fpu_operand', 'memory_disjoint', 'iop_import_audit'}
FIELDS = {'ft':16, 'fs':11}


def sha(data): return hashlib.sha256(data).hexdigest()


def eligible(rule, identity):
    return rule['status']=='active' and (rule['scope']=='shared' or identity in rule['executable_sha256'])


def expression(rule, word, before):
    if word & int(rule['opcode_mask'],16) != int(rule['opcode_value'],16):
        raise ValueError('Expression does not match R5900 opcode: '+rule['id'])
    fd=(word>>6)&31
    def emitted(field):
        return f"ctx->f[{fd}] = {rule['helper']}(ctx->f[{(word>>FIELDS[field])&31}]);"
    expected=emitted(rule['operand'])
    if before not in {expected,emitted(rule['legacy_operand'])}:
        raise ValueError('Unexpected FPU translation: '+rule['id'])
    return expected


def validate(bank):
    if bank.get('schema')!=1 or type(bank.get('revision')) is not int or bank['revision']<1:
        raise ValueError('Unsupported knowledge schema/revision')
    if not isinstance(bank.get('rules'),list) or len(bank['rules'])>512:
        raise ValueError('Invalid rule list')
    ids=set(); rewrites=[]
    for rule in bank['rules']:
        ident=rule.get('id','')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',ident) or ident in ids:
            raise ValueError('Invalid/duplicate knowledge rule ID')
        ids.add(ident)
        if type(rule.get('revision')) is not int or rule['revision']<1:
            raise ValueError('Invalid rule revision')
        if rule.get('kind') not in KINDS or rule.get('scope') not in {'shared','title'} or rule.get('status') not in {'active','proposed'}:
            raise ValueError('Unknown rule handler/scope/status')
        if not rule.get('evidence'): raise ValueError('Knowledge needs provenance')
        if rule['scope']=='title':
            fingerprints=rule.get('executable_sha256',[])
            if not fingerprints or any(not re.fullmatch(r'[a-f0-9]{64}',s) for s in fingerprints):
                raise ValueError('Title knowledge requires explicit executable revisions')
        elif 'executable_sha256' in rule:
            raise ValueError('Shared rule cannot carry title fingerprints')
        if rule['kind']=='fpu_operand':
            if not re.fullmatch(r'FPU_[A-Z0-9_]+',rule.get('helper','')) or rule.get('operand') not in FIELDS or rule.get('legacy_operand') not in FIELDS:
                raise ValueError('Invalid FPU rule fields')
            for key in ['opcode_mask','opcode_value']:
                if not re.fullmatch(r'[a-f0-9]{8}',rule.get(key,'')): raise ValueError('Expected 32-bit opcode predicate')
            mask,value=int(rule['opcode_mask'],16),int(rule['opcode_value'],16)
            # This handler only supports COP1 single-precision unary instructions.
            if mask & 0xffe0003f != 0xffe0003f or value & 0xffe00000 != 0x46000000 or value & ~mask:
                raise ValueError('Unbounded or unsupported FPU predicate')
            if rule['status']=='active':
                if len(rule.get('fixtures',[]))<2: raise ValueError('Active rewrite needs failing and idempotent fixtures')
                changed=stable=False
                for fixture in rule['fixtures']:
                    result=expression(rule,int(fixture['word'],16),fixture['before'])
                    if result!=fixture['after']: raise ValueError('Knowledge regression fixture failed')
                    changed |= result!=fixture['before']; stable |= result==fixture['before']
                if not changed or not stable: raise ValueError('Fixtures must cover correction and idempotence')
                for other in rewrites:
                    common=mask & int(other['opcode_mask'],16)
                    if rule['helper']==other['helper'] and ((value ^ int(other['opcode_value'],16)) & common)==0:
                        raise ValueError('Overlapping active rewrite rules')
                rewrites.append(rule)
        if rule['kind']=='mmi_pmulth':
            from mmi import rewrite, legacy, emit
            if any(r.get('kind')=='mmi_pmulth' and r is not rule and r.get('status')=='active'
                   for r in bank['rules']) and rule['status']=='active':
                raise ValueError('Overlapping PMULTH rules')
            if rule['status']=='active':
                fixtures=rule.get('fixtures',[])
                if {f.get('emission') for f in fixtures} != {'legacy','corrected'}:
                    raise ValueError('PMULTH fixtures need correction and idempotence')
                for fixture in fixtures:
                    word=int(fixture['word'],16)
                    before=legacy(word) if fixture['emission']=='legacy' else emit(word)
                    result,count,changed=rewrite(f'// 0x1000: 0x{word:08x} instruction\n'+before)
                    if count!=1 or changed!=int(fixture['emission']=='legacy') or emit(word) not in result:
                        raise ValueError('PMULTH regression fixture failed')
        if rule['kind']=='memory_disjoint' and rule['status']=='active':
            from memory import collisions
            fixtures=rule.get('fixtures',[])
            if not fixtures or not any(f['conflicts'] for f in fixtures) or not any(f['conflicts']==0 for f in fixtures):
                raise ValueError('Memory gate needs rejecting and accepting fixtures')
            for fixture in fixtures:
                if len(collisions(fixture['reserved'],fixture['occupied']))!=fixture['conflicts']:
                    raise ValueError('Memory gate regression failed')
    return bank


def load(path=None):
    path=Path(path or lab.ROOT/'knowledge.json')
    return validate(json.loads(path.read_text()))


def add_rule(bank_path,candidate_path):
    """Append a data-only rule after schema/regression/conflict checks; preserve old bank."""
    bank_path,candidate_path=Path(bank_path).resolve(),Path(candidate_path).resolve()
    raw=bank_path.read_bytes(); bank=validate(json.loads(raw))
    candidate=json.loads(candidate_path.read_text())
    if candidate.get('scope')=='title' and bank_path.is_relative_to(lab.ROOT):
        raise ValueError('Title-specific knowledge belongs in a private bank, not reusable source')
    # Append-only IDs; changing an existing rule requires a separately reviewed bank revision.
    if any(r['id']==candidate.get('id') for r in bank['rules']): raise ValueError('Rule ID already exists')
    new=dict(bank, revision=bank['revision']+1, rules=bank['rules']+[candidate])
    validate(new)
    archive=bank_path.parent/'knowledge-history'/f"{bank['revision']}-{sha(raw)}.json"
    archive.parent.mkdir(exist_ok=True)
    if archive.exists() and archive.read_bytes()!=raw: raise ValueError('Knowledge history collision')
    if not archive.exists(): archive.write_bytes(raw)
    # Reject concurrent edits instead of overwriting another worker's additions.
    if bank_path.read_bytes()!=raw: raise ValueError('Knowledge changed during validation')
    lab.save(bank_path,new)
    return dict(revision=new['revision'],added=candidate['id'],previous_sha256=sha(raw),sha256=lab.digest(bank_path))


def apply_translation(directory, identity=None, bank=None, only=None, write=True):
    bank=validate(bank) if bank is not None else load()
    directory=Path(directory).resolve(); pending=[]; records=[]
    selected=[r for r in bank['rules'] if r['kind'] in {'fpu_operand','mmi_pmulth'} and eligible(r,identity) and (only is None or r['id'] in only)]
    totals=dict(instructions=0,corrected=0,files_changed=0)
    # Shared inventory: nested generated C++ fails closed rather than escaping the rules.
    for path in lab.generated_sources(directory):
        if path.is_symlink() or not path.resolve().is_relative_to(directory): raise ValueError('Unsafe generated source')
        raw=path.read_bytes(); original=raw.decode(); updated=original; applied=[]
        for rule in selected:
            if rule['kind']=='mmi_pmulth':
                from mmi import rewrite
                updated,count,changed=rewrite(updated)
                totals['instructions']+=count; totals['corrected']+=changed
                if count: applied.append(dict(id=rule['id'],matched=count,changed=changed))
                continue
            pattern=re.compile(r'(// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]+)[^\n]*\n(?:[ \t]*ctx->pc[^\n]*\n)?[ \t]*)'
                r'(ctx->f\[\d+\] = '+re.escape(rule['helper'])+r'\(ctx->f\[\d+\]\);)')
            count=changed=0
            def replace(match):
                nonlocal count,changed
                result=expression(rule,int(match[2],16),match[3]); count+=1; changed+=result!=match[3]
                return match[1]+result
            updated=pattern.sub(replace,updated)
            # Unrecognized emission cannot silently escape the active rule.
            if len(re.findall(re.escape(rule['helper'])+r'\s*\(',updated)) != count:
                raise ValueError('FPU helper without a recognized instruction record: '+path.name)
            totals['instructions']+=count; totals['corrected']+=changed
            if count: applied.append(dict(id=rule['id'],matched=count,changed=changed))
        if applied:
            records.append(dict(file=path.name,before_sha256=sha(raw),after_sha256=sha(updated.encode()),rules=applied))
        if updated!=original: pending.append((path,raw,updated.encode()))
    # Validate every source before any mutation. Then reject concurrent file edits.
    for path,raw,updated in pending:
        if path.read_bytes()!=raw: raise ValueError('Generated source changed during rule evaluation')
    if write:
        for path,raw,updated in pending:
            temp=path.with_name(path.name+'.knowledge.tmp'); temp.write_bytes(updated); temp.replace(path)
    totals['files_changed']=len(pending)
    return dict(schema=1,bank_revision=bank['revision'],bank_sha256=sha(json.dumps(bank,sort_keys=True).encode()),
                totals=totals,files=records,write=write)


def import_tables(raw):
    """Bounded IOP import discovery in ELF allocated sections; no filename-based HLE claims."""
    if len(raw)<52 or raw[:6]!=b'\x7fELF\x01\x01' or struct.unpack_from('<H',raw,18)[0]!=8:
        raise ValueError('Expected little-endian MIPS ELF module')
    offset=struct.unpack_from('<I',raw,32)[0]; stride,count=struct.unpack_from('<HH',raw,46)
    if not count or stride<40 or offset+stride*count>len(raw): raise ValueError('Invalid IRX section table')
    rows=[]
    for i in range(count):
        _,kind,flags,_,start,size,*_=struct.unpack_from('<10I',raw,offset+i*stride)
        if kind==8 or not flags&2: continue
        if start+size>len(raw): raise ValueError('IRX section exceeds input')
        end=start+size
        for at in range(start,end-19,4):
            if raw[at:at+4]!=b'\0\0\xe0A': continue
            name=raw[at+12:at+20].split(b'\0',1)[0]
            if not re.fullmatch(rb'[A-Za-z0-9_]{1,8}',name): continue
            ordinals=[]; cursor=at+20; terminated=False
            for _ in range(1024):
                if cursor+8>end: break
                first,second=struct.unpack_from('<II',raw,cursor);cursor+=8
                if first==second==0: terminated=True; break
                if first!=0x03e00008 or second&0xffff0000!=0x24000000: break
                ordinals.append(second&0xffff)
            if ordinals and terminated:
                rows.append(dict(library=name.decode(),version=struct.unpack_from('<H',raw,at+8)[0],
                    ordinals=ordinals,file_offset=at))
    return rows


def plan(profile,private):
    """Apply known detection/gate handlers and expose unqualified contracts per new title."""
    private=Path(private).resolve(); bank=load(); identity=lab.digest(lab.boot_executable(private/'disc-root',profile))
    rules=[r for r in bank['rules'] if eligible(r,identity)]
    report=dict(schema=1,local_only=True,bank_revision=bank['revision'],bank_sha256=lab.digest(lab.ROOT/'knowledge.json'),
        executable_sha256=identity,active_rules=[r['id'] for r in rules],modules=[],gaps=[],memory=None)
    if any(r['kind']=='iop_import_audit' for r in rules):
        for path in sorted((private/'disc-root').rglob('*')):
            if path.suffix.lower()!='.irx' or not path.is_file(): continue
            row=dict(file=str(path.relative_to(private/'disc-root')),sha256=lab.digest(path))
            try:
                if path.is_symlink() or path.stat().st_size>64*1024*1024: raise ValueError('Unsafe/oversized module')
                row['imports']=import_tables(path.read_bytes())
                row['status']='contracts_detected' if row['imports'] else 'no_tables_detected_not_proof_of_no_imports'
            except ValueError as error: row.update(status='unsupported_module',error=str(error))
            report['modules'].append(row)
        if report['modules']: report['gaps'].append('IOP imports require version/ordinal-specific runtime conformance; detection is not implementation')
    if any(r['kind']=='memory_disjoint' for r in rules):
        supplied=profile.get('engine_memory')
        if not supplied:
            report['gaps'].append('Provide engine_memory runtime_header and layout from the actual selected build; no guessed free addresses')
        else:
            from memory import elf_regions, pools, region, collisions
            def local(key):
                p=(private/lab.safe_relative(supplied[key])).resolve()
                if not p.is_relative_to(private) or not p.is_file(): raise ValueError('Memory gate input escapes private storage')
                return p
            header,layout=local('runtime_header'),local('layout')
            occupied=elf_regions(lab.boot_executable(private/'disc-root',profile).read_bytes())
            occupied += [region(x['name'],int(x['start'],0),int(x['size'],0)) for x in json.loads(layout.read_text())['occupied']]
            reserved=pools(header); conflicts=collisions(reserved,occupied)
            report['memory']=dict(accepted=not conflicts,conflicts=conflicts,header_sha256=lab.digest(header),layout_sha256=lab.digest(layout))
            if conflicts: report['gaps'].append('Runtime reservations overlap guest memory: native build blocked')
    report['native_build_allowed']=False # Full current runtime clean-build capability is not yet registered.
    lab.save(private/'analysis/knowledge-plan.json',report)
    return report
