"""Package an already accepted local GPU app; never rebuild or publish game code."""
from pathlib import Path
import json
import plistlib
import re
import shutil
import subprocess
import pipeline as lab


def package(source, destination, title, version, bundle_id, icon, log):
    source, destination, icon, log = map(lambda p: Path(p).resolve(), (source, destination, icon, log))
    if source == destination or destination.exists():
        raise ValueError('Use a new destination; accepted apps are never overwritten')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', bundle_id) or not re.fullmatch(r'\d+(?:\.\d+)*', version):
        raise ValueError('Invalid bundle identifier or numeric version')
    if not icon.is_file() or icon.suffix != '.icns':
        raise ValueError('A local .icns icon is required')
    config = plistlib.loads((source/'Contents/Resources/RunConfiguration.plist').read_bytes())
    info = plistlib.loads((source/'Contents/Info.plist').read_bytes())
    settings = info.get('LSEnvironment', {})
    # Deliberately only copy normal, declared settings; no inherited shell flags.
    allowed_private = {'PS2_PORT_PRIVATE_IOP_IMPORT_CYCLES', 'PS2_PORT_PRIVATE_SDR_GUEST_ONLY'}
    for key, value in settings.items():
        if not isinstance(value, str) or not (key.startswith('PS2_PORT_ENHANCE_') or key == 'PS2_PORT_TEX_PACK' or key in allowed_private):
            raise ValueError('Unexpected diagnostic or unknown release setting: '+key)
    for key in ['Executable', 'DiscDirectory']:
        if not Path(config[key]).is_absolute() or not Path(config[key]).exists():
            raise ValueError('Missing local asset dependency: '+key)
    if settings.get('PS2_PORT_TEX_PACK') and not Path(settings['PS2_PORT_TEX_PACK']).is_dir():
        raise ValueError('Missing local texture pack')
    pinned = ['Contents/MacOS/ps2EntryRunner', 'Contents/Frameworks/liblocal-parallel-gs-plugin.dylib',
              'Contents/Frameworks/libMoltenVK.dylib']
    original = {name: lab.digest(source/name) for name in pinned}
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    signature = destination/'Contents/_CodeSignature'
    if signature.exists(): shutil.rmtree(signature)
    resources = destination/'Contents/Resources'
    shutil.copy2(icon, resources/'AppIcon.icns')
    config.update(BuildIdentifier='v'+version+'-local-release', Environment=settings, LogFile=str(log),
                  LocalDiagnostics=False, GraphicsHistoryEnabled=False)
    config.pop('GraphicsHistory', None)
    (resources/'RunConfiguration.plist').write_bytes(plistlib.dumps(config))
    info.pop('LSEnvironment', None)
    info.update(CFBundleName=title, CFBundleDisplayName=title, CFBundleIdentifier=bundle_id,
                CFBundleVersion=version, CFBundleShortVersionString=version,
                CFBundleIconFile='AppIcon', CFBundleExecutable='LocalLauncher',
                NSHighResolutionCapable=True, LSApplicationCategoryType='public.app-category.games')
    (destination/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run(['/usr/bin/clang', '-framework', 'AppKit', '-framework', 'Foundation',
                    str(lab.ROOT/'cmake/PlayableLauncher.m'), '-o', str(destination/'Contents/MacOS/LocalLauncher')], check=True)
    for name in pinned:
        if lab.digest(destination/name) != original[name]:
            raise RuntimeError('Packaging changed the accepted game engine: '+name)
    # Leaf signatures remain untouched; sign only the new shell and bundle seal.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(destination/'Contents/MacOS/LocalLauncher')], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(destination)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
    report = dict(schema=1, local_only=True, status='packaged_signature_verified',
        source_app=str(source), app=str(destination), title=title, version=version,
        accepted_engine_sha256=original, source_info_sha256=lab.digest(source/'Contents/Info.plist'),
        configuration_sha256=lab.digest(resources/'RunConfiguration.plist'),
        icon_sha256=lab.digest(resources/'AppIcon.icns'),
        dependencies={'disc':config['DiscDirectory'], 'texture_pack':settings.get('PS2_PORT_TEX_PACK')},
        asset_storage='external local project paths; app may move, project assets must remain',
        clean_rebuild_verified=False, launch_smoke_verified=False)
    lab.save(destination.with_suffix('.release.json'), report)
    return report
