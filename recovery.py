"""Bounded original execution, readable recovery and private cross-title reuse.

No game execution is accepted on resemblance alone. Successful reports mean
agreement on explicit finite cases in this integer/RAM envelope, never universal
hardware equivalence or automatic runtime activation.
"""
from pathlib import Path
import dataclasses
import hashlib
import json
import platform
import re
import shutil
import struct
import subprocess

from recovery_core import emu, symexec as sx

ROOT = Path(__file__).resolve().parent
MASK64 = (1 << 64) - 1
MAX_CODE = 256
MAX_MEMORY = 65536
MAX_CASES = 64
MIN_CASES = 8
SHARED_BANK = ROOT/'.private/recovery-bank'
CORE = ('emu.py','vu0.py','layout.py','r5900.py','symexec.py')
REG_ARGS = tuple(range(4,12))
PRESERVED = tuple(range(16,24))+(28,29,30,31)
# Nontrapping integer core only. No calls, branches, hardware, floats, SIMD,
# division, HI/LO or external-service hooks can enter via the public adapter.
SPECIAL = {0x00,0x02,0x03,0x21,0x23,0x24,0x25,0x26,0x2a,0x2b}
IMMEDIATE = {0x09,0x0c,0x0d,0x0e,0x0f}
LOADS = {0x20:(1,True),0x21:(2,True),0x23:(4,True),0x24:(1,False),0x25:(2,False),0x27:(4,False)}
STORES = {0x28:1,0x29:2,0x2b:4}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def implementation():
    origin=json.loads((ROOT/'recovery_core/ORIGIN.json').read_text())
    actual={n:sha((ROOT/'recovery_core'/n).read_bytes()) for n in CORE}
    if actual != origin['files']:
        raise ValueError('Pinned recovery core changed')
    return dict(core_revision=origin['revision'],core=actual,
                adapter_sha256=sha(Path(__file__).read_bytes()),
                package_sha256=sha((ROOT/'recovery_core/__init__.py').read_bytes()),
                origin_sha256=sha((ROOT/'recovery_core/ORIGIN.json').read_bytes()),
                qualification_fixture_sha256=sha((ROOT/'tests/fixtures/integer-oracle.json').read_bytes()),
                runner_sha256=sha((ROOT/'tools/recovery_runner.hpp').read_bytes()))


def u32(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{8}',value):
        raise ValueError('Expected eight lowercase hexadecimal address digits')
    return int(value,16)


def validate_code(code,entry):
    if type(entry) is not int or entry&3 or entry<4 or entry+len(code)>0x02000000:
        raise ValueError('Code must be aligned inside main RAM')
    if not 8 <= len(code) <= MAX_CODE or len(code)%4:
        raise ValueError('Expected 2-64 complete instructions')
    words=list(struct.unpack('<'+'I'*(len(code)//4),code))
    if words[-2] != 0x03e00008:
        raise ValueError('Leaf must end with JR RA and its delay slot')
    stored=False
    for i,w in enumerate(words):
        if i==len(words)-2:continue
        op=w>>26;rs=(w>>21)&31;rd=(w>>11)&31;sa=(w>>6)&31;fn=w&63
        if op==0 and fn in SPECIAL:
            if (fn in (0,2,3) and rs) or (fn not in (0,2,3) and sa):
                raise ValueError('Reserved integer encoding')
        elif op in IMMEDIATE:
            if op==15 and rs:raise ValueError('Reserved LUI encoding')
        elif op in LOADS:
            if stored:raise ValueError('Loads after stores are not qualified, including return delay slots')
        elif op in STORES:stored=True
        else:raise ValueError(f'Unsupported integer-leaf word {w:08x}')
    return words


def normalize(code):
    """Candidate filter inspired by GT4 dedup; kill tracked highs on overwrite.

    LUI and possible paired low immediates are masked, not proved relocations.
    This key never authorizes reuse without the target's execution comparisons.
    """
    words=struct.unpack('<'+'I'*(len(code)//4),code);out=[];high=set()
    for w in words:
        op=w>>26;rs=(w>>21)&31;rt=(w>>16)&31;rd=(w>>11)&31
        paired=rs in high and op in ({9,13}|set(LOADS)|set(STORES))
        out.append(w&0xffff0000 if op==15 or paired else w)
        dest=rd if op==0 and (w&63)!=8 else rt if op in (IMMEDIATE|set(LOADS)) else 0
        if dest:high.discard(dest)
        if op==15 and rt:high.add(rt)
    return sha(struct.pack('<'+'I'*len(out),*out))


class Memory:
    """Only immutable code and one explicit RAM region. No zero-filled MMIO."""
    def __init__(self,code,entry,base,data):
        self.code,self.entry,self.base=code,entry,base
        self.data=bytearray(data);self.writes=[]
    def offset(self,addr,size):
        if size not in (1,2,4) or addr&(size-1) or not self.base<=addr or addr+size>self.base+len(self.data):
            raise ValueError('Unmapped or unaligned RAM access')
        return addr-self.base
    def read_int(self,addr,size,signed=False):
        if size==4 and self.entry<=addr and addr+4<=self.entry+len(self.code):
            return int.from_bytes(self.code[addr-self.entry:addr-self.entry+4],'little',signed=signed)
        off=self.offset(addr,size)
        return int.from_bytes(self.data[off:off+size],'little',signed=signed)
    def write_int(self,addr,value,size):
        off=self.offset(addr,size);value&=(1<<(size*8))-1
        self.data[off:off+size]=value.to_bytes(size,'little')
        self.writes.append([addr,size,value])


def case_value(case,entry,size):
    if not isinstance(case,dict) or set(case)!={'args','memory'} or len(case['args'])!=8:
        raise ValueError('Each case needs exactly eight integer arguments and explicit memory')
    if any(not isinstance(x,str) or not re.fullmatch('[0-9a-f]{16}',x) for x in case['args']):
        raise ValueError('Arguments must be 16-digit lowercase hexadecimal words')
    mem=case['memory']
    if not isinstance(mem,dict) or set(mem)!={'base','hex'}:raise ValueError('Invalid memory case')
    base=u32(mem['base']);raw=mem['hex']
    if not isinstance(raw,str) or len(raw)>MAX_MEMORY*2 or not re.fullmatch('(?:[0-9a-f]{2})*',raw):
        raise ValueError('Invalid bounded RAM bytes')
    data=bytes.fromhex(raw)
    if base+len(data)>0x02000000 or (data and base<entry+size and entry<base+len(data)):
        raise ValueError('RAM case overlaps code or leaves main RAM')
    return [int(x,16) for x in case['args']],base,data


def original(code,entry,case,budget=128):
    validate_code(code,entry)
    if type(budget) is not int or not 1<=budget<=128:raise ValueError('Invalid instruction budget')
    args,base,data=case_value(case,entry,len(code))
    # Every case constructs fresh state; interpreter caches decode by PC+word.
    machine=emu.Machine(entry,code,total_size=len(code))
    memory=Memory(code,entry,base,data);machine.memory=memory
    for reg,arg in zip(REG_ARGS,args):machine.set(reg,arg)
    for reg in PRESERVED:machine.set(reg,0x1122334400000000+reg)
    machine.set(28,0);machine.set(29,0x01f00000);machine.set(31,emu.RETURN_SENTINEL)
    initial={reg:machine.get(reg) for reg in PRESERVED}
    machine.pc=entry;steps=0
    while machine.pc!=emu.RETURN_SENTINEL:
        if steps>=budget:raise ValueError('Original execution exhausted instruction budget')
        if machine.pc&3 or not entry<=machine.pc<entry+len(code):raise ValueError('Original left admitted code')
        machine.step();steps+=1
    if machine.stubbed_calls:raise ValueError('Original invoked an external stub')
    changed=[reg for reg in PRESERVED if machine.get(reg)!=initial[reg]]
    if changed:raise ValueError('Original violates qualified callee-saved ABI: '+str(changed))
    return dict(result=f'{machine.get(2):016x}',memory=bytes(memory.data).hex(),writes=memory.writes,
                steps=steps,preserved_registers_checked=list(PRESERVED),stubbed_calls=0)


# Only these expression nodes are accepted from the symbolic core or a private
# reuse record. No source strings, Python expressions or native code are loaded.
NODES={'Arg':sx.Arg,'Const':sx.Const,'Load':sx.Load,'Binary':sx.Binary,
       'SignExtend32':sx.SignExtend32,'Compare':sx.Compare}
OPS={'add','sub','shl','shr','sar','and','or','xor'}


def encode(node,depth=0,budget=None):
    budget=[2048] if budget is None else budget;budget[0]-=1
    if depth>32 or budget[0]<0 or type(node).__name__ not in NODES:
        raise ValueError('Expression outside qualified node/size envelope')
    out={'kind':type(node).__name__}
    for f in dataclasses.fields(node):
        value=getattr(node,f.name)
        out[f.name]=encode(value,depth+1,budget) if isinstance(value,sx.Expr) else value
    return out


def decode(node,depth=0,budget=None):
    budget=[2048] if budget is None else budget;budget[0]-=1
    if not isinstance(node,dict) or depth>32 or budget[0]<0 or node.get('kind') not in NODES:
        raise ValueError('Invalid recovery expression')
    cls=NODES[node['kind']];fields={f.name for f in dataclasses.fields(cls)}
    if set(node)!={'kind'}|fields:raise ValueError('Unexpected expression fields')
    values={k:decode(v,depth+1,budget) if isinstance(v,dict) else v for k,v in node.items() if k!='kind'}
    child_fields={'Load':('address',),'Binary':('left','right'),'Compare':('left','right'),'SignExtend32':('value',)}.get(node['kind'],())
    if any(not isinstance(values[k],sx.Expr) for k in child_fields):
        raise ValueError('Invalid expression child')
    result=cls(**values)
    if isinstance(result,sx.Arg) and (type(result.index) is not int or not 0<=result.index<8):raise ValueError('Invalid argument index')
    if isinstance(result,sx.Const) and (type(result.value) is not int or not 0<=result.value<=MASK64):raise ValueError('Invalid constant')
    if isinstance(result,sx.Binary) and result.op not in OPS:raise ValueError('Unsupported expression operation')
    if isinstance(result,sx.Compare) and result.op not in ('lt','ltu'):raise ValueError('Unsupported comparison')
    if isinstance(result,sx.Load) and (result.size not in (1,2,4) or type(result.signed) is not bool or result.epoch!=0):
        raise ValueError('Unsupported load expression')
    # Required children cannot be strings or other literals masquerading as AST.
    for name in ('left','right','value','address'):
        if name in fields and not (name=='value' and isinstance(result,sx.Const)) and not isinstance(getattr(result,name),sx.Expr):
            raise ValueError('Invalid expression child')
    return result


def derive(code,entry):
    validate_code(code,entry)
    try:derived=sx.derive(code,entry,entry,entry+len(code),gp_value=0)
    except sx.Unsupported as e:raise ValueError('Readable recovery unsupported: '+str(e)) from e
    recipe=dict(expression=encode(sx.simplify(derived.expression)),stores=[])
    for store in derived.stores:
        if store.size not in (1,2,4):raise ValueError('Unsupported store width')
        recipe['stores'].append(dict(address=encode(sx.simplify(store.address)),value=encode(sx.simplify(store.value)),size=store.size))
    return recipe


def expression(node):
    n=decode(node) if isinstance(node,dict) else node
    if isinstance(n,sx.Arg):return f'arg{n.index}'
    if isinstance(n,sx.Const):return f'0x{n.value:016x}ull'
    if isinstance(n,sx.SignExtend32):return f'u64(s64(s32(u32({expression(n.value)}))))'
    if isinstance(n,sx.Load):return f'm.read(u32({expression(n.address)}), {n.size}, {str(n.signed).lower()})'
    if isinstance(n,sx.Compare):
        a,b=expression(n.left),expression(n.right)
        return f'u64(s64({a}) < s64({b}))' if n.op=='lt' else f'u64(({a}) < ({b}))'
    if isinstance(n,sx.Binary):
        a,b=expression(n.left),expression(n.right)
        if n.op=='shl':return f'u64(u32({a}) << (u32({b}) & 31u))'
        if n.op=='shr':return f'u64(u32({a}) >> (u32({b}) & 31u))'
        if n.op=='sar':return f'u64(s64(s32(u32({a})) >> (u32({b}) & 31u)))'
        return f'(({a}) '+{'add':'+','sub':'-','and':'&','or':'|','xor':'^'}[n.op]+f' ({b}))'
    raise ValueError('Unsupported expression')


def emit(recipe):
    if not isinstance(recipe,dict) or set(recipe)!={'expression','stores'} or not isinstance(recipe['stores'],list) or len(recipe['stores'])>64:
        raise ValueError('Invalid recovery recipe')
    lines=['u64 recovered(Memory &m, '+', '.join(f'u64 arg{i}' for i in range(8))+') {']
    # Snapshot all expressions before the first store so aliases retain the
    # original pre-store loads. The adapter refuses any source load after stores.
    lines.append('  const u64 result = '+expression(recipe['expression'])+';')
    for i,store in enumerate(recipe['stores']):
        if set(store)!={'address','value','size'} or store['size'] not in (1,2,4):raise ValueError('Invalid store')
        lines+=['  const u32 address'+str(i)+' = u32('+expression(store['address'])+');',
                '  const u64 value'+str(i)+' = '+expression(store['value'])+';']
    for i,store in enumerate(recipe['stores']):lines.append(f'  m.write(address{i}, value{i}, {store["size"]});')
    lines+=['  return result;','}']
    return '\n'.join(lines)+'\n'


def compare(expected,actual):
    if len(expected)!=len(actual) or not expected:raise ValueError('No complete one-to-one case comparisons')
    differences=[]
    for i,(a,b) in enumerate(zip(expected,actual)):
        fields=[k for k in ('result','memory','writes') if a[k]!=b[k]]
        if fields:differences.append(dict(case=i,fields=fields))
    return dict(cases=len(expected),matched=len(expected)-len(differences),differences=differences,
                accepted_on_cases=not differences,universal_equivalence=False)


def run_native(recipe,cases,directory,compiler=None):
    directory=Path(directory);source=directory/'recovered.cpp';exe=directory/('recovered.exe' if platform.system()=='Windows' else 'recovered')
    source.write_text('#include "recovery_runner.hpp"\n'+emit(recipe)+'\nRECOVERY_MAIN\n')
    compiler=compiler or shutil.which('clang++') or shutil.which('g++')
    if not compiler:raise RuntimeError('A C++20 compiler is required for actual comparisons')
    flags=['-std=c++20','-O1','-fno-fast-math','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all']
    command=[compiler,*flags,'-I',str(ROOT/'tools'),str(source),'-o',str(exe)]
    built=subprocess.run(command,capture_output=True,text=True,timeout=60)
    (directory/'compile.log').write_text(built.stdout+built.stderr)
    if built.returncode:raise RuntimeError('Recovered C++ compile failed; see private compile.log')
    payload=[]
    for case in cases:
        mem=case['memory'];payload.append(' '.join(case['args']+[mem['base'],str(len(mem['hex'])//2),mem['hex'] or '-']))
    ran=subprocess.run([str(exe)],input='\n'.join(payload)+'\n',capture_output=True,text=True,timeout=15)
    (directory/'native.log').write_text(ran.stdout+ran.stderr)
    if ran.returncode:raise RuntimeError('Recovered execution failed; comparisons not accepted')
    actual=[]
    for line in ran.stdout.splitlines():
        value,memory,writes=line.split('|')
        actual.append(dict(result=value,memory='' if memory=='-' else memory,writes=json.loads(writes)))
    version=subprocess.run([compiler,'--version'],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
    return actual,dict(path=str(Path(compiler).absolute()),version=version,flags=flags,
                       source_sha256=sha(source.read_bytes()),binary_sha256=sha(exe.read_bytes()))


def qualify_oracle():
    path=ROOT/'tests/fixtures/integer-oracle.json';fixture=json.loads(path.read_text())
    if fixture.get('schema')!=1 or len(fixture.get('cases',[]))<24:
        raise ValueError('Missing independent oracle qualification cases')
    failures=[]
    for n,entry in enumerate(fixture['cases']):
        result=original(bytes.fromhex(entry['code']),u32(entry['entry']),entry['case'])
        if any(result[k]!=entry['expected'][k] for k in ('result','memory','writes')):failures.append(n)
    if failures:raise ValueError('Original interpreter failed fixed expected cases: '+str(failures))
    return dict(status='passed_fixed_integer_vectors',cases=len(fixture['cases']),failures=0,
        fixture_sha256=sha(path.read_bytes()),hardware_rerun=False,full_interpreter_qualified=False,
        scope='Hand-authored architectural regression values for the admitted integer/RAM subset; not a console rerun')


def load_spec(profile,private,relative):
    import pipeline as lab
    from feedback import private_file,executable_spans
    path=private_file(private,relative,10<<20);raw=path.read_bytes();spec=json.loads(raw)
    if not isinstance(spec,dict) or set(spec)!={'schema','executable_sha256','function','cases'} or spec['schema']!=1:
        raise ValueError('Invalid recovery specification')
    elf=lab.boot_executable(Path(private)/'disc-root',profile)
    elf=private_file(private,str(elf.relative_to(Path(private))),64<<20)
    image=elf.read_bytes();identity=sha(image)
    if spec['executable_sha256']!=identity or (profile.get('expected_sha256') and profile['expected_sha256']!=identity):
        raise ValueError('Recovery specification targets another executable revision')
    f=spec['function']
    if not isinstance(f,dict) or set(f)!={'address','bytes'} or type(f['bytes']) is not int:
        raise ValueError('Invalid function span')
    entry=u32(f['address']);size=f['bytes']
    if size<8 or size>MAX_CODE:raise ValueError('Function exceeds admitted code budget')
    matches=[(a,p) for a,b,p in executable_spans(image) if a<=entry and entry+size<=b]
    if len(matches)!=1:raise ValueError('Function is not inside one file-backed executable segment')
    address,offset=matches[0];code=image[offset+entry-address:offset+entry-address+size]
    validate_code(code,entry)
    cases=spec['cases']
    if not isinstance(cases,list) or not MIN_CASES<=len(cases)<=MAX_CASES:
        raise ValueError('Recovery requires 8-64 complete explicit cases')
    if len({json.dumps(c,sort_keys=True) for c in cases})!=len(cases):raise ValueError('Duplicate cases do not establish new coverage')
    for c in cases:case_value(c,entry,len(code))
    return code,entry,cases,dict(spec_path=str(path.relative_to(Path(private))),spec_sha256=sha(raw),
        executable_sha256=identity,function=f,case_set_sha256=sha(json.dumps(cases,sort_keys=True).encode()))


def bank_path(private,relative,create=False):
    from pipeline import safe_relative
    private=Path(private).resolve()
    if relative=='@shared':
        path=SHARED_BANK;private=SHARED_BANK.parent
        if private.is_symlink():raise ValueError('Shared private bank root is a symlink')
    else:path=private/safe_relative(relative)
    if not path.resolve().is_relative_to(private.resolve()) or any(p.is_symlink() for p in [path,*path.parents] if p!=private and p.is_relative_to(private)):
        raise ValueError('Recovery bank must be a private non-symlink directory')
    if create:path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not path.is_dir():raise ValueError('Recovery bank is missing')
    return path


def read_record(bank,key):
    from feedback import private_file
    if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('Expected full candidate SHA-256')
    path=private_file(bank,key+'.json',2<<20);raw=path.read_bytes()
    if sha(raw)!=key:raise ValueError('Recovery record content hash differs')
    r=json.loads(raw)
    if r.get('schema')!=1 or r.get('implementation')!=implementation() or r.get('status')!='agreement_on_cases':
        raise ValueError('Recovery record needs requalification for this implementation')
    code=bytes.fromhex(r['code_hex']);validate_code(code,u32(r['function']['address']))
    if sha(code)!=r.get('code_sha256') or normalize(code)!=r.get('masked_sha256'):
        raise ValueError('Recovery record code identity differs')
    emit(r['recipe'])
    return r


def matches(private,code,bank_relative):
    bank=bank_path(private,bank_relative);files=list(bank.iterdir())
    if len(files)>1024:raise ValueError('Recovery bank exceeds entry budget')
    out=[]
    for path in sorted(files):
        if path.suffix!='.json':raise ValueError('Unexpected recovery bank file')
        r=read_record(bank,path.stem)
        if r['code_sha256']==sha(code) or r['masked_sha256']==normalize(code):
            out.append(dict(candidate=path.stem,match='exact_code' if r['code_sha256']==sha(code) else 'masked_candidate',
                source_executable_sha256=r['executable_sha256'],source_function=r['function'],
                accepted_for_target=False))
    return out


def relocate(recipe,mapping):
    if not isinstance(mapping,dict) or len(mapping)>64:raise ValueError('Invalid explicit relocation bindings')
    bindings={u32(k):u32(v) for k,v in mapping.items()}
    used=set()
    def walk(node):
        if isinstance(node,dict):
            result={k:walk(v) for k,v in node.items()}
            if result.get('kind')=='Const' and result.get('value') in bindings:
                used.add(result['value']);result['value']=bindings[result['value']]
            return result
        if isinstance(node,list):return [walk(x) for x in node]
        return node
    transferred=walk(recipe)
    if used!=set(bindings):raise ValueError('Relocation binding did not match a complete recovered constant')
    emit(transferred)
    return transferred


def run(profile,private,spec_relative,name,bank_relative=None,candidate=None,bindings_relative=None):
    import pipeline as lab
    from compiler import identifier
    from feedback import private_file
    identifier(name);private=Path(private).resolve()
    code,entry,cases,inputs=load_spec(profile,private,spec_relative)
    model=implementation();qualification=qualify_oracle()
    origin=None
    if candidate:
        if not bank_relative:raise ValueError('Reuse requires an explicit private recovery bank')
        bank=bank_path(private,bank_relative);source=read_record(bank,candidate)
        if source['code_sha256']!=sha(code) and source['masked_sha256']!=normalize(code):
            raise ValueError('Candidate does not match target instruction shape')
        mapping={}
        if bindings_relative:
            bindings=private_file(private,bindings_relative,16384)
            mapping=json.loads(bindings.read_text());inputs['bindings_sha256']=sha(bindings.read_bytes())
        recipe=relocate(source['recipe'],mapping)
        origin=dict(candidate=candidate,source_executable_sha256=source['executable_sha256'],
                    match='exact_code' if source['code_sha256']==sha(code) else 'masked_candidate')
    else:
        if bindings_relative:raise ValueError('Bindings apply only to an explicit reuse candidate')
        recipe=derive(code,entry)
    out=private/'analysis/function-recovery'/name
    if not out.resolve().is_relative_to(private) or any(p.is_symlink() for p in [out,*out.parents] if p!=private and p.is_relative_to(private)):
        raise ValueError('Unsafe recovery output')
    out.mkdir(parents=True,mode=0o700,exist_ok=False)
    report=dict(schema=1,title_id=profile['id'],name=name,inputs=inputs,implementation=model,
        original_qualification=qualification,code_sha256=sha(code),masked_sha256=normalize(code),
        provenance=origin,scope='Finite pure integer-leaf ABI-return and explicit RAM comparisons; no whole-game or universal equivalence',
        promoted_to_runtime=False,universal_equivalence=False,hardware_rerun=False,local_only=True,
        shared_decoder_blind_spot=True,status='incomplete')
    try:
        expected=[original(code,entry,c) for c in cases]
        actual,host=run_native(recipe,cases,out)
        assessment=compare(expected,actual)
        report.update(assessment,host_compiler=host,status='agreement_on_cases' if assessment['accepted_on_cases'] else 'failed')
        (out/'recipe.json').write_text(json.dumps(recipe,sort_keys=True,indent=2)+'\n')
        # Re-read evidence after execution, rather than issuing a receipt for
        # a different executable/spec/implementation edited during comparison.
        _,_,again,now=load_spec(profile,private,spec_relative)
        if now!=inputs and not bindings_relative:raise ValueError('Recovery inputs changed during execution')
        if {k:v for k,v in inputs.items() if k!='bindings_sha256'}!=now or again!=cases or implementation()!=model:
            raise ValueError('Recovery inputs changed during execution')
        if bindings_relative and sha(private_file(private,bindings_relative,16384).read_bytes())!=inputs['bindings_sha256']:
            raise ValueError('Relocation bindings changed during execution')
        if candidate:read_record(bank_path(private,bank_relative),candidate)
        if assessment['accepted_on_cases'] and bank_relative:
            bank=bank_path(private,bank_relative,create=True)
            if len(list(bank.iterdir()))>=1024:raise ValueError('Recovery bank exceeds entry budget')
            record=dict(schema=1,status='agreement_on_cases',implementation=model,recipe=recipe,
                code_hex=code.hex(),code_sha256=sha(code),masked_sha256=normalize(code),
                executable_sha256=inputs['executable_sha256'],function=inputs['function'],
                case_set_sha256=inputs['case_set_sha256'],cases=len(cases),qualification=qualification,
                source_sha256=host['source_sha256'],provenance=origin,universal_equivalence=False)
            raw=(json.dumps(record,sort_keys=True,indent=2)+'\n').encode();key=sha(raw);target=bank/(key+'.json')
            if target.exists():
                if target.is_symlink() or target.read_bytes()!=raw:raise ValueError('Recovery record collision')
            else:
                with target.open('xb') as handle:handle.write(raw)
            report['library_record']=key
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        report.update(status='failed',accepted_on_cases=False,error=str(error))
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        raise
