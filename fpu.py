"""Opcode/shape-guarded DIV.S and RSQRT.S correction, adapted from upstream #272.

RSQRT uses both operands. DIV preserves the existing generated zero-divisor flag
update; scalar helpers improve result values, not full FCR31 exception semantics.
"""
import re
from mmi import shape

MASK=0xffe0003f
VALUES={0x46000003:'div',0x46000016:'rsqrt'}

FINITE_HELPER='''static inline float claude_ps2_div0(float fs, float ft) { uint32_t a, b; std::memcpy(&a, &fs, 4); std::memcpy(&b, &ft, 4);
    const uint32_t r = ((a ^ b) & 0x80000000u) | 0x7F7FFFFFu; float f; std::memcpy(&f, &r, 4); return f; }'''


def finite_helper_block(text):
    blocks=list(re.finditer(r'(?ms)^#ifndef CLAUDE_PS2_DIV0\s*\n.*?^#endif[^\n]*\n',text))
    if len(blocks)!=1:return None
    body=re.sub(r'//[^\n]*|/\*.*?\*/','',blocks[0][0],flags=re.S)
    canonical='#ifndef CLAUDE_PS2_DIV0\n#define CLAUDE_PS2_DIV0\n#include <cstring>\n'+FINITE_HELPER+'\n#endif'
    return blocks[0] if shape(canonical).fullmatch(body.strip()) else None


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


def reference_call(word):
    """The call the patched recompiler emits (patches/fpu-reference-flags.patch); already correct."""
    fd,fs,ft=registers(word)
    return f'ctx->f[{fd}] = ps2_fpu::{VALUES[word&MASK]}(ctx, ctx->f[{fs}], ctx->f[{ft}]);'


def rewrite(text):
    records=list(re.finditer(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n',text))
    edits=[];count=0;finite_block=finite_helper_block(text)
    for i,record in enumerate(records):
        word=int(record[1],16)
        if word&MASK not in VALUES: continue
        count+=1
        end=records[i+1].start() if i+1<len(records) else len(text)
        start=record.end()
        prefix=re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?',text[start:end])
        start+=prefix.end();body=text[start:end]
        if shape(emit(word)).match(body) or shape(reference_call(word)).match(body): continue
        old=shape(legacy(word)).match(body)
        if not old and VALUES[word&MASK]=='div' and finite_block:
            fd,fs,ft=registers(word)
            finite=legacy(word).replace(f'copysignf(INFINITY, ctx->f[{fs}] * 0.0f)',f'claude_ps2_div0(ctx->f[{fs}], ctx->f[{ft}])')
            old=shape(finite).match(body)
        if not old: raise ValueError('Unrecognized scalar FPU emission; refusing speculative rewrite')
        edits.append((start,start+old.end(),emit(word)))
    for start,end,value in reversed(edits):text=text[:start]+value+text[end:]
    # Remove only this fully recognized, now-unused compatibility helper. A
    # same-named helper with different semantics is never accepted or erased.
    finite_block=finite_helper_block(text)
    if finite_block and len(re.findall(r'\bclaude_ps2_div0\b',text))==1:
        text=text[:finite_block.start()]+text[finite_block.end():]
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
