"""Upgrade recognized scalar-FPU headers without replacing private macros."""
from pathlib import Path
import hashlib,os,re
from clean_build import path_in

LEGACY_HELPERS={'7c1d1bf6dad97d71480ec1b275d31aa019f7410b0144c8cae4f3a3cc2453f763'}
RULES={
 'FPU_ADD_S':(('((float)(a) + (float)(b))',),'ps2_port_fpu::ps2_fadd(a, b)'),
 'FPU_SUB_S':(('((float)(a) - (float)(b))',),'ps2_port_fpu::ps2_fsub(a, b)'),
 'FPU_MUL_S':(('((float)(a) * (float)(b))',),'ps2_port_fpu::ps2_fmul(a, b)'),
 'FPU_DIV_S':(('((float)(a) / (float)(b))',),'ps2_port_fpu::ps2_fdiv(a, b)'),
 'FPU_SQRT_S':(('sqrtf((float)(a))','sqrtf(fabsf((float)(a)))'),'ps2_port_fpu::ps2_fsqrt(a)'),
 'FPU_C_EQ_S':(('((float)(a) == (float)(b))',),'ps2_port_fpu::ps2_feq(a, b)'),
 'FPU_C_OLT_S':(('((float)(a) < (float)(b))',),'ps2_port_fpu::ps2_flt(a, b)'),
 'FPU_C_OLE_S':(('((float)(a) <= (float)(b))',),'ps2_port_fpu::ps2_fle(a, b)'),
 'FPU_C_LT_S':(('((float)(a) < (float)(b))',),'ps2_port_fpu::ps2_flt(a, b)'),
 'FPU_C_LE_S':(('((float)(a) <= (float)(b))',),'ps2_port_fpu::ps2_fle(a, b)'),
}
def compact(text):return re.sub(r'\s+','',text)


def upgrade_macro_header(text,include):
    updated=text
    for name,(old,new) in RULES.items():
        pattern=re.compile(r'^#define\s+'+name+r'\(([^\n)]*)\)[ \t]+([^\n]+)$',re.M)
        found=list(pattern.finditer(updated))
        if len(found)!=1:raise ValueError('Missing/ambiguous scalar macro: '+name)
        match=found[0];parameters='a' if name=='FPU_SQRT_S' else 'a,b'
        if compact(match[1])!=parameters or compact(match[2]) not in {compact(x) for x in (*old,new)}:raise ValueError('Unrecognized scalar macro: '+name)
        updated=updated[:match.start()]+f'#define {name}({parameters}) {new}'+updated[match.end():]
    # Resolve the helper inside the pinned graph, independent of -I ordering.
    updated=re.sub(r'^#include[ \t]+"(?:'+re.escape(include)+r'|[^"\n]*ps2_port_scalar_fpu\.h)"[ \t]*\n','',updated,flags=re.M)
    return f'#include "{include}"\n'+updated


def plan(root,files):
    root=Path(root).resolve();headers=[n for n in files if Path(n).name=='ps2_runtime_macros.h']
    if not headers:return dict(edits=[],blockers=[],headers=[])
    helpers=sorted(n for n in files if Path(n).name=='ps2_port_scalar_fpu.h')
    if not helpers:return dict(edits=[],blockers=[dict(reason='scalar_helper_missing_from_graph')],headers=headers)
    current=(Path(__file__).parent/'tools/ps2_scalar_fpu.h').read_bytes();current_pin=hashlib.sha256(current).hexdigest()
    edits=[];blockers=[]
    for name in helpers:
        raw=path_in(root,name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=files[name]:raise ValueError('Scalar helper changed during qualification')
        if files[name] not in LEGACY_HELPERS|{current_pin}:blockers.append(dict(staged=name,reason='unrecognized_retained_scalar_helper'));continue
        if raw!=current:edits.append(dict(file=name,sha256=files[name],before=raw.decode(),after=current.decode(),reason='Known public scalar helper version upgraded to current qualified engine helper'))
    for name in headers:
        raw=path_in(root,name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=files[name]:raise ValueError('Scalar macro header changed during qualification')
        try:
            text=raw.decode();companion=str(Path(name).parent/'ps2_port_scalar_fpu.h')
            helper=companion if companion in helpers else helpers[0]
            include=os.path.relpath(root/helper,(root/name).parent).replace(os.sep,'/')
            updated=upgrade_macro_header(text,include)
        except (ValueError,UnicodeDecodeError) as error:blockers.append(dict(staged=name,reason='unqualified_scalar_macro_header',detail=str(error)));continue
        if updated!=text:edits.append(dict(file=name,sha256=files[name],before=text,after=updated,reason='Recognized scalar arithmetic/comparison macros use the pinned engine helper; private macros preserved'))
    return dict(edits=edits,blockers=blockers,headers=headers)
