"""Explicit optional platform adapter contract; discovery never imports adapter code."""
from pathlib import Path
import argparse
import json
import os
import platform
import subprocess
import sys

API_VERSION = 1


def inspect_adapter(path):
    path = Path(path).resolve()
    adapter = json.loads(path.read_text())
    if adapter.get('schema') != 1 or adapter.get('core_api') != API_VERSION:
        raise ValueError('Unsupported adapter schema or core API')
    if not isinstance(adapter.get('id'), str) or not adapter['id'].replace('-', '').isalnum():
        raise ValueError('Invalid adapter ID')
    systems = adapter.get('systems')
    if not isinstance(systems, list) or not systems or any(s not in {'Darwin', 'Linux', 'Windows'} for s in systems):
        raise ValueError('Invalid adapter host systems')
    relative = adapter.get('entrypoint')
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Adapter entrypoint must be relative')
    entry = path.parent / relative
    if entry.is_symlink() or not entry.resolve().is_relative_to(path.parent) or not entry.is_file() or entry.suffix != '.py':
        raise ValueError('Adapter entrypoint escapes its repository or is missing')
    return adapter, entry.resolve()


def run_adapter(path, arguments, system=None):
    adapter, entry = inspect_adapter(path)
    if (system or platform.system()) not in adapter['systems']:
        raise ValueError('Adapter does not support this host')
    env = os.environ.copy()
    env['PS2_PORT_LAB_CORE'] = str(Path(__file__).resolve().parent)
    # An adapter is executable local code. Use only an explicitly installed,
    # reviewed adapter; JSON validation does not sandbox its implementation.
    return subprocess.run([sys.executable, str(entry), *arguments], env=env).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', required=True)
    parser.add_argument('--describe', action='store_true')
    options, arguments = parser.parse_known_args()
    if options.describe:
        print(json.dumps(inspect_adapter(options.adapter)[0], indent=2))
        return
    raise SystemExit(run_adapter(options.adapter, arguments))


if __name__ == '__main__':
    main()
