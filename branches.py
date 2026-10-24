"""Opcode-checked EE signed branch correction; never rewrite 32-bit arithmetic."""
import re

RECORD = re.compile(r'// 0x[0-9a-fA-F]+: 0x([0-9a-fA-F]{1,8})[^\n]*\n')


def predicate(word):
    op, rs, rt = word >> 26, (word >> 21) & 31, (word >> 16) & 31
    if op in (6, 7, 22, 23) and rt == 0:
        return rs, '<=' if op in (6, 22) else '>'
    if op == 1 and rt in (0, 1, 2, 3, 16, 17, 18, 19):
        return rs, '<' if rt % 2 == 0 else '>='
    return None


def rewrite(text):
    records = list(RECORD.finditer(text))
    edits = []
    count = 0
    for i, record in enumerate(records):
        decoded = predicate(int(record[1], 16))
        if decoded is None:
            continue
        rs, operator = decoded
        count += 1
        end = records[i + 1].start() if i + 1 < len(records) else len(text)
        body = text[record.end():end]
        pattern = re.compile(r'GPR_S(32|64)\(ctx,\s*' + str(rs) + r'\)\s*' + re.escape(operator) + r'\s*0\b')
        matches = list(pattern.finditer(body))
        if len(matches) != 1:
            raise ValueError('Unrecognized signed-branch emission; refusing an unqualified rewrite')
        match = matches[0]
        if match[1] == '32':
            at = record.end() + match.start(1)
            edits.append((at, at + 2, '64'))
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text, count, len(edits)


def qualify():
    """Independent two's-complement controls, including zero-extended VU signs."""
    rows = []
    for value in (0, 1, 0x7fffffff, 0x80000000, 0xffffffff, 0x100000000,
                  0x7fffffffffffffff, 0x8000000000000000, 0xffffffffffffffff):
        signed = value if value < (1 << 63) else value - (1 << 64)
        for op, compare in (('<', lambda x: x < 0), ('>=', lambda x: x >= 0),
                            ('<=', lambda x: x <= 0), ('>', lambda x: x > 0)):
            rows.append(dict(value=f'{value:016x}', operator=op, expected=compare(signed)))
    return rows
