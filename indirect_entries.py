"""Check native entry coverage for register jumps into decoded guest code."""
from pathlib import Path
import re


def audit(directory):
    root = Path(directory)
    tables = list(root.glob('*register_functions.cpp'))
    if len(tables) != 1:
        raise ValueError('Indirect entry audit requires one registration table')
    covered = {int(at, 16) for at in re.findall(
        r'\[[0-9]+u?\]\s*=\s*\w+\s*;\s*//\s*0x([0-9a-fA-F]+)', tables[0].read_text())}
    findings = []
    checked = 0
    for source in root.glob('*.cpp'):
        decoded = [(int(pc,16),int(word,16)) for pc,word in re.findall(
            r'// 0x([0-9a-fA-F]+): 0x([0-9a-fA-F]{1,8})\b', source.read_text())]
        jumps = [pc for pc,word in decoded
                 if word >> 26 == 0 and word & 63 == 8 and (word >> 21) & 31 != 31]
        if not jumps:
            continue
        checked += 1
        missing = sorted({pc for pc,_ in decoded} - covered)
        if missing:
            findings.append(dict(file=source.name, jump_sites=[f'{pc:08x}' for pc in jumps],
                                 missing_count=len(missing), missing_sample=[f'{pc:08x}' for pc in missing[:16]]))
    return dict(schema=1, passed=not findings, functions_checked=checked, findings=findings,
                scope='Decoded non-return JR entry coverage only; no inferred instructions or gameplay certification')
