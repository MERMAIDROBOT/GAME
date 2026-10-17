"""One entry point: shared PS2-to-C++ engine with optional platform targets."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
from platform_adapter import inspect_adapter, run_adapter

ROOT = Path(__file__).resolve().parent


def available_targets():
    targets = {'cpp': {'id': 'cpp', 'description': 'PS2 executable to generated C++', 'systems': ['Darwin', 'Linux', 'Windows']}}
    for path in sorted((ROOT/'platforms').glob('*/adapter.json')):
        adapter, _ = inspect_adapter(path)
        if adapter['id'] in targets:
            raise ValueError('Duplicate target ID')
        targets[adapter['id']] = dict(adapter, manifest=str(path))
    return targets


def dispatch(target, arguments):
    targets = available_targets()
    if target not in targets:
        raise ValueError('Unknown target; use --list-targets')
    if target == 'cpp':
        return subprocess.run([sys.executable, str(ROOT/'target_cli.py'), 'cpp', *arguments]).returncode
    return run_adapter(targets[target]['manifest'], arguments)


def main(argv=None):
    # add_help=False: -h/--help is forwarded to the selected target, so the real
    # subcommand list (and per-command help) is reachable from this entry point.
    parser = argparse.ArgumentParser(description=__doc__, add_help=False,
        epilog='Examples: run.py --help; run.py doctor; run.py --target macos doctor; '
               'run.py --profile .private/profiles/<id>.json status. See README.md.')
    parser.add_argument('--target', default='cpp', help='Output target (default: cpp)')
    parser.add_argument('--list-targets', action='store_true')
    options, arguments = parser.parse_known_args(argv)
    if options.list_targets:
        print(json.dumps(available_targets(), indent=2))
        return 0
    if not arguments or arguments[0] in {'-h', '--help'}:
        parser.print_help()
        print(f'\nCommands for target {options.target!r}:\n', flush=True)
        return dispatch(options.target, arguments or ['--help'])
    return dispatch(options.target, arguments)


def cli(argv=None):
    """Errors raised before a target takes over (unknown target, invalid adapter) exit cleanly."""
    try:
        return main(argv)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(cli())
