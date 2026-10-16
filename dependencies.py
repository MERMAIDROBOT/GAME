"""Read Clang make dependency files without executing make or expanding variables."""
from pathlib import Path
import hashlib
import re


def dependency_paths(text, directory):
    # Clang emits a single target rule, optional continued lines and escaped
    # filename characters. Reject make expressions rather than evaluate them.
    text = text.replace('\\\r\n', '').replace('\\\n', '')
    escaped = False
    colon = None
    for i, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == ':':
            colon = i
            break
    if colon is None:
        raise ValueError('Dependency rule has no target separator')
    words, current = [], []
    tail = text[colon+1:]
    i = 0
    while i < len(tail):
        character = tail[i]
        if character == '\\':
            i += 1
            if i == len(tail):
                raise ValueError('Truncated dependency escape')
            current.append(tail[i])
        elif character == '$':
            if i+1 >= len(tail) or tail[i+1] != '$':
                raise ValueError('Dependency variable expressions are unsupported')
            current.append('$')
            i += 1
        elif character == '#':
            raise ValueError('Unescaped dependency comment is unsupported')
        elif character == ':':
            raise ValueError('Multiple dependency rules are unsupported')
        elif character.isspace():
            if current:
                words.append(''.join(current))
                current = []
        else:
            current.append(character)
        i += 1
    if current:
        words.append(''.join(current))
    if not words:
        raise ValueError('Dependency rule is empty')
    return list(dict.fromkeys((Path(directory)/word).resolve() for word in words))


def verify_files(records):
    """Verify declared local project/toolchain pins; never execute manifest text."""
    mismatches = []
    for record in records:
        expected = record['sha256']
        if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise ValueError('Invalid dependency SHA-256')
        path = Path(record['path'])
        if not path.is_absolute():
            raise ValueError('Dependency verification requires absolute local paths')
        if not path.is_file():
            mismatches.append(dict(path=str(path), reason='missing'))
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(1024*1024):
                digest.update(block)
        if digest.hexdigest() != expected:
            mismatches.append(dict(path=str(path), reason='changed'))
    return mismatches
