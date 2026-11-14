"""Check native entry coverage for register jumps into decoded guest code."""
from pathlib import Path
import re


def _signed16(word):
    value = word & 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _control_transfer(word, pc):
    """(is a branch or jump with a delay slot, its in-function target or None)."""
    op = word >> 26
    if op in (2, 3):                                   # J, JAL
        return True, ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
    if op in (4, 5, 6, 7, 20, 21, 22, 23):             # BEQ BNE BLEZ BGTZ and their likely forms
        return True, pc + 4 + (_signed16(word) << 2)
    if op == 1 and ((word >> 16) & 31) in (0, 1, 2, 3, 16, 17, 18, 19):   # REGIMM branches
        return True, pc + 4 + (_signed16(word) << 2)
    if op in (16, 17, 18) and ((word >> 21) & 31) == 8:                    # COPz BC
        return True, pc + 4 + (_signed16(word) << 2)
    if op == 0 and (word & 63) in (8, 9):              # JR, JALR
        return True, None
    return False, None


def block_starts(decoded):
    """Where a register jump can land in a function: its first instruction, every branch or jump target inside
    it and the instruction after each delay slot - the starts of the blocks a compiler places code at. A jump
    table, even one a game rewrites at run time, holds such addresses (2026-11-12: every decoded instruction as
    an entry made the functions holding a jump table - 68 in Kelly Slater, its menu input among them - slow)."""
    pcs = {pc for pc, _ in decoded}
    starts = {min(pcs)} if pcs else set()
    for pc, word in decoded:
        transfer, target = _control_transfer(word, pc)
        if not transfer:
            continue
        if pc + 8 in pcs:
            starts.add(pc + 8)
        if target is not None and target in pcs:
            starts.add(target)
    return starts


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
        missing = sorted(block_starts(decoded) - covered)
        if missing:
            findings.append(dict(file=source.name, jump_sites=[f'{pc:08x}' for pc in jumps],
                                 missing_count=len(missing), missing_sample=[f'{pc:08x}' for pc in missing[:16]]))
    return dict(schema=1, passed=not findings, functions_checked=checked, findings=findings,
                scope='Block starts of functions with a non-return JR; no inferred instructions or gameplay certification')
