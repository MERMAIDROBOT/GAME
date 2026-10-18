"""Opcode/shape-guarded DIV.S and RSQRT.S correction, adapted from upstream #272.

RSQRT uses both operands. DIV preserves the existing generated zero-divisor flag
update; scalar helpers improve result values, not full FCR31 exception semantics.
"""
import re
from mmi import shape

MASK=0xffe0003f
VALUES={0x46000003:'div',0x46000016:'rsqrt'}


def registers(word):
    if word & MASK not in VALUES:
        raise ValueError('Expected R5900 DIV.S or RSQRT.S')
    return (word>>6)&31,(word>>11)&31,(word>>16)&31


def legacy(word):
    fd,fs,ft=registers(word)
    if VALUES[word&MASK]=='rsqrt':
        return f'ctx->f[{fd}] = 1.0f / sqrtf(ctx->f[{fs}]);'
    return (f'if (ctx->f[{ft}] == 0.0f) {{ ctx->fcr31 |= 0x100000; /* DZ flag */ '
            f'ctx->f[{fd}] = copysignf(INFINITY, ctx->f[{fs}] * 0.0f); }} '
            f'else ctx->f[{fd}] = ctx->f[{fs}] / ctx->f[{ft}];')


def emit(word):
    fd,fs,ft=registers(word)
    if VALUES[word&MASK]=='rsqrt':
        # Existing macros keep C++ export syntax-compatible with the pinned header.
        return f'ctx->f[{fd}] = FPU_DIV_S(ctx->f[{fs}], FPU_SQRT_S(ctx->f[{ft}]));'
    return (f'{{ if (ctx->f[{ft}] == 0.0f) ctx->fcr31 |= 0x100000; /* DZ flag */ '
            f'ctx->f[{fd}] = FPU_DIV_S(ctx->f[{fs}], ctx->f[{ft}]); }}')


def rewrite(text):
    records=list(re.finditer(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n',text))
    edits=[];count=0
    for i,record in enumerate(records):
        word=int(record[1],16)
        if word&MASK not in VALUES: continue
        count+=1
        end=records[i+1].start() if i+1<len(records) else len(text)
        start=record.end()
        prefix=re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?',text[start:end])
        start+=prefix.end();body=text[start:end]
        if shape(emit(word)).match(body): continue
        old=shape(legacy(word)).match(body)
        if not old: raise ValueError('Unrecognized scalar FPU emission; refusing speculative rewrite')
        edits.append((start,start+old.end(),emit(word)))
    for start,end,value in reversed(edits):text=text[:start]+value+text[end:]
    return text,count,len(edits)


def nested_sqrt_calls(text):
    """Count only canonical RSQRT expressions tied to their own opcode record.

    SQRT's older knowledge handler rejects unproven helper calls. RSQRT now
    legitimately composes that helper, so exempt only this exact guarded shape.
    """
    records=list(re.finditer(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n',text))
    count=0
    for i,record in enumerate(records):
        w=int(record[1],16)
        if w&MASK != 0x46000016:continue
        end=records[i+1].start() if i+1<len(records) else len(text)
        body=text[record.end():end]
        prefix=re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?',body)
        count+=bool(shape(emit(w)).match(body[prefix.end():]))
    return count
