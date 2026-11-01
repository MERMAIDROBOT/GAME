"""Opcode-guarded VU0 macro-mode corrections for the pinned PS2Recomp emission shape.

VCALLMSR always starts the microprogram at CMSAR0 (control register vi27, in units
of 8 bytes). The pinned translator read ctx->vi[27], but R5900Context::vi holds only
VI0-VI15, so the call jumped to whatever followed the array. CTC2 to vi27 already
stores ctx->vu0_cmsar0, which the corrected emission reads. Found on SSX 3 (terrain
collision and culling helpers); no game code or title addresses are required.
"""
import re

MASK = 0xffe007ff   # COP2 CO, special1 VCALLMSR; rt/is fields are not decoded by hardware
VALUE = 0x4a000039
MARKER = 'port-lab VCALLMSR v1'
VI_COUNT = 16       # R5900Context::vi entries


def check(word):
    if word & MASK != VALUE:
        raise ValueError('Expected VU0 VCALLMSR opcode')
    return (word >> 11) & 31


def legacy(word):
    vis = check(word)
    return ('{     uint16_t instr_index = ctx->vi[' + str(vis) + '] & 0x1FF; '
            '    uint32_t target_byte_addr = (uint32_t)instr_index << 3; '
            '    ctx->vu0_pc = target_byte_addr; '
            '    runtime->vu0StartMicroProgram(rdram, ctx, target_byte_addr); }')


def emit(word):
    check(word)
    return ('{ // ' + MARKER + ': start address comes from CMSAR0\n'
            '    uint16_t instr_index = static_cast<uint16_t>(ctx->vu0_cmsar0) & 0x1FF;\n'
            '    uint32_t target_byte_addr = (uint32_t)instr_index << 3;\n'
            '    ctx->vu0_pc = target_byte_addr;\n'
            '    runtime->vu0StartMicroProgram(rdram, ctx, target_byte_addr); }')


def shape(text):
    return re.compile(r'\s*'.join(re.escape(part) for part in re.split(r'\s+', text.strip())))


def rewrite(text):
    record = re.compile(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n')
    matches = list(record.finditer(text))
    edits = []
    count = changed = 0
    for i, match in enumerate(matches):
        word = int(match[1], 16)
        if word & MASK != VALUE:
            continue
        count += 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        start = match.end()
        prefix = re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?', text[start:end])
        start += prefix.end()
        body = text[start:end]
        if shape(emit(word)).match(body):
            continue
        old = shape(legacy(word)).match(body)
        if not old:
            raise ValueError('Unrecognized VCALLMSR emission; refusing speculative rewrite')
        edits.append((start, start + old.end(), emit(word)))
        changed += 1
    if text.count(MARKER) > count:
        raise ValueError('VCALLMSR correction without matching opcode provenance')
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text, count, changed


def vi_index_violations(text):
    """Indices of ctx->vi[N] with N outside VI0-VI15 (always an out-of-bounds read/write)."""
    return sorted({int(n) for n in re.findall(r'ctx->vi\[(\d+)\]', text) if int(n) >= VI_COUNT})


# --- Macro-mode VU0 data-memory transfers -------------------------------------------------
# VLQI/VSQI/VLQD/VSQD/VILWR/VISWR address VU0 data memory (4 KB, 256 quadwords) by VI, in
# quadword units. The pinned translator emitted main-RAM accesses at vi*16 (or vi*4) and, for
# the store forms, swapped the VF and VI fields, so a title's matrix uploads (SSX 3's view and
# clip matrices for its VU0 patch classifier) never reached the VU. Corrected emission reads and
# writes runtime->memory().getVU0Data() directly with the per-lane dest mask.
MEM_MASK = 0xfe0007ff
MEM_OPS = {0x4a00037c: 'vlqi', 0x4a00037d: 'vsqi', 0x4a00037e: 'vlqd', 0x4a00037f: 'vsqd',
           0x4a0003fe: 'vilwr', 0x4a0003ff: 'viswr'}
MEM_MARKER = 'port-lab VU0MEM v1'


def mem_fields(word):
    op = MEM_OPS.get(word & MEM_MASK)
    if op is None:
        raise ValueError('Expected a VU0 macro memory opcode')
    return op, (word >> 21) & 15, (word >> 16) & 31, (word >> 11) & 31


def _lanes(dest):
    return [lane for lane in range(4) if dest & (8 >> lane)]  # bit 3 = x ... bit 0 = w


def mem_legacy(word):
    op, dest, rt, rd = mem_fields(word)
    mask = ', '.join('-1' if dest & bit else '0' for bit in (1, 2, 4, 8))
    if op == 'vilwr':
        return f'{{ uint32_t addr = (uint32_t)(ctx->vi[{rd}] << 2) & 0x3FFC; ctx->vi[{rt}] = static_cast<uint16_t>(READ32(addr)); }}'
    if op == 'viswr':
        return f'{{ uint32_t addr = (uint32_t)(ctx->vi[{rd}] << 2) & 0x3FFC; WRITE32(addr, (uint32_t)ctx->vi[{rt}]); }}'
    load = op in ('vlqi', 'vlqd')
    pre = f'ctx->vi[{rd}] = (ctx->vi[{rd}] - 1) & 0x3FF; ' if op in ('vlqd', 'vsqd') else ''
    post = f' ctx->vi[{rd}] = (ctx->vi[{rd}] + 1) & 0x3FF;' if op in ('vlqi', 'vsqi') else ''
    addr = f'uint32_t addr = ((uint32_t)(ctx->vi[{rd}] & 0x3FF)) << 4; '
    if load:
        body = (f'__m128 res = _mm_castsi128_ps(READ128(addr)); __m128i mask = _mm_set_epi32({mask}); '
                f'ctx->vu0_vf[{rt}] = _mm_blendv_ps(ctx->vu0_vf[{rt}], res, _mm_castsi128_ps(mask));')
    else:
        body = (f'__m128i old_val = READ128(addr); __m128 res = _mm_blendv_ps(_mm_castsi128_ps(old_val), '
                f'ctx->vu0_vf[{rt}], _mm_castsi128_ps(_mm_set_epi32({mask}))); WRITE128(addr, _mm_castps_si128(res));')
    return '{ ' + pre + addr + body + post + ' }'


def mem_emit(word):
    op, dest, rt, rd = mem_fields(word)
    lanes = _lanes(dest)
    lines = [f'{{ // {MEM_MARKER}: {op} addresses VU0 data memory in quadwords',
             '    uint8_t *ps2_vu0 = runtime->memory().getVU0Data();']
    if op in ('vilwr', 'viswr'):
        it, vis = rt & 15, rd & 15
        lane = lanes[0] if lanes else 0
        lines.append(f'    const uint32_t ps2_off = ((uint32_t(ctx->vi[{vis}]) & 0xFFu) << 4) + {4 * lane}u;')
        if op == 'vilwr':
            if it:
                lines.append(f'    uint32_t ps2_w; std::memcpy(&ps2_w, ps2_vu0 + ps2_off, 4); ctx->vi[{it}] = static_cast<uint16_t>(ps2_w);')
        else:
            lines.append(f'    const uint32_t ps2_w = ctx->vi[{it}]; std::memcpy(ps2_vu0 + ps2_off, &ps2_w, 4);')
        return '\n'.join(lines) + ' }'
    load = op in ('vlqi', 'vlqd')
    vf, vi = (rt, rd & 15) if load else (rd, rt & 15)
    if op in ('vlqd', 'vsqd') and vi:
        lines.append(f'    ctx->vi[{vi}] = static_cast<uint16_t>(ctx->vi[{vi}] - 1);')
    lines.append(f'    const uint32_t ps2_off = (uint32_t(ctx->vi[{vi}]) & 0xFFu) << 4;')
    if load:
        if vf:
            lines.append('    alignas(16) float ps2_v[4]; _mm_store_ps(ps2_v, ctx->vu0_vf[%d]);' % vf)
            lines += [f'    std::memcpy(&ps2_v[{lane}], ps2_vu0 + ps2_off + {4 * lane}u, 4);' for lane in lanes]
            lines.append(f'    ctx->vu0_vf[{vf}] = _mm_load_ps(ps2_v);')
    else:
        lines.append(f'    alignas(16) float ps2_v[4]; _mm_store_ps(ps2_v, ctx->vu0_vf[{vf}]);')
        lines += [f'    std::memcpy(ps2_vu0 + ps2_off + {4 * lane}u, &ps2_v[{lane}], 4);' for lane in lanes]
    if op in ('vlqi', 'vsqi') and vi:
        lines.append(f'    ctx->vi[{vi}] = static_cast<uint16_t>(ctx->vi[{vi}] + 1);')
    return '\n'.join(lines) + ' }'


def mem_rewrite(text):
    record = re.compile(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{8})[^\n]*\n')
    matches = list(record.finditer(text))
    edits = []
    count = changed = 0
    for i, match in enumerate(matches):
        word = int(match[1], 16)
        if (word & MEM_MASK) not in MEM_OPS:
            continue
        count += 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        start = match.end()
        prefix = re.match(r'\s*(?:ctx->pc\s*=\s*0x[0-9a-fA-F]+u?;\s*)?', text[start:end])
        start += prefix.end()
        body = text[start:end]
        if shape(mem_emit(word)).match(body):
            continue
        old = shape(mem_legacy(word)).match(body)
        if not old:
            raise ValueError('Unrecognized VU0 macro memory emission; refusing speculative rewrite')
        edits.append((start, start + old.end(), mem_emit(word)))
        changed += 1
    if text.count(MEM_MARKER) > count:
        raise ValueError('VU0 memory correction without matching opcode provenance')
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text, count, changed
