"""Opcode-guarded PMULTH correction for the pinned PS2Recomp emission shape.

Semantics checked against PCSX2 MMI.cpp; God of War's PMULTH investigation
identified the defect. No game code or title addresses are required.
"""
import re

MASK = 0xfc0007ff
VALUE = 0x70000709


def registers(word):
    if word & MASK != VALUE:
        raise ValueError('Expected R5900 PMULTH opcode')
    return (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31


def legacy(word):
    rs,rt,rd = registers(word)
    return f'''{{ __m128i prod = _mm_madd_epi16(GPR_VEC(ctx, {rs}), GPR_VEC(ctx, {rt}));
   int32_t p0 = _mm_cvtsi128_si32(prod);
   int32_t p1 = _mm_cvtsi128_si32(_mm_srli_si128(prod, 4));
   int32_t p2 = _mm_cvtsi128_si32(_mm_srli_si128(prod, 8));
   int32_t p3 = _mm_cvtsi128_si32(_mm_srli_si128(prod, 12));
   int64_t result = (int64_t)p0 + (int64_t)p1 + (int64_t)p2 + (int64_t)p3;
   ctx->lo = (uint32_t)result; ctx->hi = (uint32_t)(result >> 32);
   SET_GPR_U64(ctx, {rd}, result); }}'''


def emit(word):
    rs,rt,rd = registers(word)
    # Snapshot both operands before any destination write; aliases are legal.
    return f'''{{ // port-lab PMULTH v1
    auto ps2_a_vec = GPR_VEC(ctx, {rs});
    auto ps2_b_vec = GPR_VEC(ctx, {rt});
    int16_t ps2_a[8], ps2_b[8];
    int32_t ps2_p[8];
    std::memcpy(ps2_a, &ps2_a_vec, sizeof(ps2_a));
    std::memcpy(ps2_b, &ps2_b_vec, sizeof(ps2_b));
    for (unsigned ps2_i = 0; ps2_i < 8; ++ps2_i)
        ps2_p[ps2_i] = int32_t(ps2_a[ps2_i]) * int32_t(ps2_b[ps2_i]);
    ctx->lo = uint64_t(uint32_t(ps2_p[0])) | (uint64_t(uint32_t(ps2_p[1])) << 32);
    ctx->hi = uint64_t(uint32_t(ps2_p[2])) | (uint64_t(uint32_t(ps2_p[3])) << 32);
    ctx->lo1 = uint64_t(uint32_t(ps2_p[4])) | (uint64_t(uint32_t(ps2_p[5])) << 32);
    ctx->hi1 = uint64_t(uint32_t(ps2_p[6])) | (uint64_t(uint32_t(ps2_p[7])) << 32);
    SET_GPR_VEC(ctx, {rd}, _mm_setr_epi32(ps2_p[0], ps2_p[2], ps2_p[4], ps2_p[6]));
}}'''


def shape(text):
    return re.compile(r'\s*'.join(re.escape(part) for part in re.split(r'\s+',text.strip())))


def rewrite(text):
    record = re.compile(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n')
    matches = list(record.finditer(text))
    edits = []
    count = changed = 0
    for i,match in enumerate(matches):
        word = int(match[1],16)
        if word & MASK != VALUE:
            continue
        count += 1
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        start = match.end()
        prefix = re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?',text[start:end])
        start += prefix.end()
        body = text[start:end]
        corrected = emit(word)
        if shape(corrected).match(body):
            continue
        old = shape(legacy(word)).match(body)
        if not old:
            raise ValueError('Unrecognized PMULTH emission; refusing speculative rewrite')
        edits.append((start,start+old.end(),corrected))
        changed += 1
    # A correction marker cannot silently lose its instruction provenance.
    if text.count('port-lab PMULTH v1') > count:
        raise ValueError('PMULTH correction without matching opcode provenance')
    for start,end,replacement in reversed(edits):
        text = text[:start]+replacement+text[end:]
    return text,count,changed
