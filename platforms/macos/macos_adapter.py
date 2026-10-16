"""macOS adapter uses the shared engine; it does not duplicate its compiler."""
from pathlib import Path
import os
import platform
import sys


def main():
    if platform.system() != 'Darwin':
        raise SystemExit('The macOS adapter requires macOS')
    core = Path(os.environ.get('PS2_PORT_LAB_CORE', Path(__file__).resolve().parents[2])).resolve()
    if not (core/'pipeline.py').is_file() or not (core/'platform_adapter.py').is_file():
        raise SystemExit('Shared engine is missing from this checkout')
    sys.path.insert(0, str(core))
    from platform_adapter import API_VERSION
    if API_VERSION != 1:
        raise SystemExit('Unsupported core API; update this adapter')
    import pipeline
    pipeline.main(target='macos')


if __name__ == '__main__':
    main()
