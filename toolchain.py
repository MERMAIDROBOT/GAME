"""Fingerprint the selected local macOS compiler/SDK; no downloads or installs."""
from pathlib import Path
import platform
import subprocess


def file_pin(path):
    from pipeline import digest
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Missing host toolchain file: '+str(path))
    return dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size)


def query(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, check=True)
    return (result.stdout+result.stderr).strip()


def capture_host():
    if platform.system() != 'Darwin':
        raise ValueError('This host toolchain capture supports macOS only')
    selected = {name:query(['/usr/bin/xcrun','--find',name]) for name in ['clang','clang++','ld','ar']}
    clang = Path(selected['clang']).resolve()
    lto = clang.parent.parent/'lib/libLTO.dylib'
    files = {name:file_pin(path) for name,path in selected.items()}
    for name,path in dict(driver_c='/usr/bin/clang', driver_cxx='/usr/bin/c++',
                          xcrun='/usr/bin/xcrun', xcode_select='/usr/bin/xcode-select',
                          codesign='/usr/bin/codesign', liblto=lto).items():
        files[name] = file_pin(path)
    sdk = Path(query(['/usr/bin/xcrun','--show-sdk-path'])).resolve()
    for name in ['SDKSettings.json','SDKSettings.plist']:
        if (sdk/name).is_file():
            files['sdk_'+name] = file_pin(sdk/name)
    return dict(schema=1, local_only=True, platform='Darwin', machine=platform.machine(),
        selection=dict(developer_directory=query(['/usr/bin/xcode-select','-p']),
            sdk=str(sdk), sdk_version=query(['/usr/bin/xcrun','--show-sdk-version']),
            resource_directory=query([str(clang),'-print-resource-dir'])),
        versions=dict(clang=query([str(clang),'--version']),
                      linker=query([selected['ld'],'-v'])), files=files,
        scope='Selected compiler/linker/SDK identity. Not all dynamic tool dependencies, '
              'build flags, project inputs, reproducibility or gameplay proof.')


def verify_host(expected, actual=None):
    if expected.get('schema') != 1 or expected.get('platform') != 'Darwin':
        raise ValueError('Unsupported host toolchain lock')
    actual = capture_host() if actual is None else actual
    differences = [key for key in ['schema','platform','machine','selection','versions','files']
                   if expected.get(key) != actual.get(key)]
    return dict(matches=not differences, differences=differences)


def record_host(private, name):
    from compiler import identifier
    from pipeline import save
    identifier(name)
    private = Path(private).resolve()
    target = private/'compiler-toolchains'/name
    if not target.resolve().is_relative_to(private):
        raise ValueError('Toolchain output escapes private storage')
    captured = capture_host()
    target.mkdir(parents=True, exist_ok=False)
    save(target/'lock.json', captured)
    return target/'lock.json'
