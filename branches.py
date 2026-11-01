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
    all_records = list(RECORD.finditer(text))
    # The translator may emit an independently resumable delay-slot copy
    # before the owning branch predicate. Its instruction comment is nested
    # inside the branch emission, not the next outer instruction boundary.
    for record in all_records:
        if '(Delay Slot)' in record[0] and predicate(int(record[1],16)) is not None:
            raise ValueError('Signed branch in a delay slot requires independent control-flow qualification')
    records = [r for r in all_records if '(Delay Slot)' not in r[0]]
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
        address = int(re.match(r'// 0x([0-9a-fA-F]+)',record[0])[1],16)
        name = r'branch_taken_0x0*' + format(address,'x') + r'\b'
        declaration = re.compile(r'\bconst\s+bool\s+'+name+r'\s*=\s*\(\s*('+pattern.pattern+r')\s*\)\s*;',re.I)
        # A delay-slot comparison may use the same register and operator.
        # For named emissions qualify the opcode-addressed predicate itself.
        if re.search(name,body,re.I):
            declarations=list(declaration.finditer(body))
            matches=[pattern.search(body,d.start(1),d.end(1)) for d in declarations]
        else:
            matches = list(pattern.finditer(body))
        if len(matches) != 1 or matches[0] is None:
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
