"""Package an already accepted local GPU app; never rebuild or publish game code."""
from pathlib import Path
import json
import plistlib
import re
import shutil
import subprocess
import pipeline as lab
from compute_slot import exclusive,assert_idle


def prepare_signing_metadata(destination, phase='initial'):
    """Remove transport metadata from our new bundle, preserving sidecars outside it.

    FAT/exFAT stores macOS extended attributes in AppleDouble files. codesign
    treats these as unsigned code when they occur beside a Mach-O executable.
    Never apply this operation to the accepted source app.
    """
    destination=Path(destination).resolve()
    from clean_build import filesystem_metadata
    sidecars=[p for p in destination.rglob('._*') if filesystem_metadata(p)]
    if not re.fullmatch('[a-z][a-z-]{0,40}',phase):raise ValueError('Invalid metadata signing phase')
    suffix='.packaging-metadata'+('' if phase=='initial' else '-'+phase)
    backup=destination.with_suffix(suffix)
    if backup.exists():raise ValueError('Preserve existing packaging metadata evidence')
    records=[]
    for path in sidecars:
        relative=path.relative_to(destination);saved=backup/relative
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,saved)
        digest=lab.digest(path)
        if lab.digest(saved)!=digest:raise RuntimeError('Metadata backup differs')
        records.append(dict(path=str(relative),sha256=digest))
    for path in sidecars:
        if path.exists():
            if not filesystem_metadata(path):raise RuntimeError('Transport metadata changed during cleanup')
            path.unlink()
    # Remove sidecars first: recursive xattr otherwise tries to clear extended
    # attributes on AppleDouble metadata itself, which exFAT rejects with EPERM.
    # Strip only the candidate's attributes; embedded Mach-O signatures and
    # every accepted engine/asset byte are checked independently afterward.
    subprocess.run(['/usr/bin/xattr','-cr',str(destination)],check=True)
    return dict(appledouble_sidecars=records,backup=str(backup) if records else None)


@exclusive('release-package')
def package(source, destination, title, version, bundle_id, icon, log, *, bundle_assets=False):
    source, destination, icon, log = map(lambda p: Path(p).resolve(), (source, destination, icon, log))
    if source == destination or destination.exists():
        raise ValueError('Use a new destination; accepted apps are never overwritten')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', bundle_id) or not re.fullmatch(r'\d+(?:\.\d+)*', version):
        raise ValueError('Invalid bundle identifier or numeric version')
    if not icon.is_file() or icon.suffix != '.icns':
        raise ValueError('A local .icns icon is required')
    metadata=['Contents/Resources/RunConfiguration.plist','Contents/Info.plist']
    metadata_pins={name:lab.digest(source/name) for name in metadata}
    config = plistlib.loads((source/metadata[0]).read_bytes())
    info = plistlib.loads((source/metadata[1]).read_bytes())
    if config.get('BundledPaths'):raise ValueError('Package from the accepted source app; assets are already bundled')
    settings = dict(config.get('Environment', {}))
    settings.update(info.get('LSEnvironment', {}))
    # Deliberately only copy normal, declared settings; no inherited shell flags.
    for key in ('PS2_PORT_PAD_TRACE','PS2_PORT_RESIDENCY_TRACE'):
        settings.pop(key,None)
    from launch_config import prepare
    # Existing deployed runners may use MC_ROOT rather than SAVE_ROOT. Preserve
    # that known local path for external-asset repackaging; do not rewrite it to
    # a different API that the accepted runner might not implement.
    if 'PS2_PORT_MC_ROOT' in settings:
        card=settings['PS2_PORT_MC_ROOT']
        if not isinstance(card,str) or not card or '\0' in card or '\n' in card or not Path(card).is_absolute():
            raise ValueError('Legacy memory-card root must be an absolute local path')
    ordinary={k:v for k,v in settings.items() if k not in {'PS2_PORT_TEX_PACK','PS2_PORT_HW_GS_PIPELINE_CACHE_DIR','PS2_PORT_SAVE_ROOT','PS2_PORT_MC_ROOT'}}
    for key in ['Executable', 'DiscDirectory']:
        if not Path(config[key]).is_absolute() or not Path(config[key]).exists():
            raise ValueError('Missing local asset dependency: '+key)
    if settings.get('PS2_PORT_TEX_PACK') and not Path(settings['PS2_PORT_TEX_PACK']).is_dir():
        raise ValueError('Missing local texture pack')
    bindings=config.get('FrameworkEnvironment')
    if bindings is None:bindings={'PS2_PORT_PRIVATE_GS_PLUGIN':'liblocal-parallel-gs-plugin.dylib','PS2_PORT_PRIVATE_MOLTENVK':'libMoltenVK.dylib'}
    if not isinstance(bindings,dict):raise ValueError('FrameworkEnvironment must be a mapping')
    names=set()
    for key,name in bindings.items():
        if key not in {'PS2_PORT_PRIVATE_GS_PLUGIN','PS2_PORT_PRIVATE_MOLTENVK'} or not isinstance(name,str) or not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',name):raise ValueError('Invalid release framework binding')
        names.add(name)
    directory=source/'Contents/Frameworks'
    if directory.is_symlink():raise ValueError('Release frameworks may not be symlinks')
    if directory.exists():
        names.update(p.name for p in directory.iterdir() if p.name.startswith('lib') and p.suffix=='.dylib')
    pinned=['Contents/MacOS/ps2EntryRunner']+['Contents/Frameworks/'+name for name in sorted(names)]
    for name in pinned:
        p=source/name
        if p.is_symlink() or not p.is_file():raise ValueError('Missing or unsafe accepted engine input: '+name)
    try:prepare(dict(native_launch=dict(environment=ordinary,framework_bindings=bindings)),source,{name:None for name in names})
    except ValueError as error:raise ValueError('Unexpected diagnostic or unknown release setting: '+str(error)) from error
    image=config.get('CdImage')
    if image is not None and (not isinstance(image,str) or not Path(image).is_absolute() or Path(image).is_symlink() or not Path(image).is_file()):raise ValueError('Missing local disc image')
    from port_checks import inspect_app
    blockers=[f for f in inspect_app(source) if f['severity']=='error' and f['code']!='diagnostic_release_setting']
    if blockers: raise ValueError('App preflight failed: '+json.dumps(blockers))
    if bundle_assets and config.get('RuntimeCapabilities',{}).get('separate_save_root') is not True:raise ValueError('Bundled assets require source-verified separate saves')
    original = {name: lab.digest(source/name) for name in pinned}
    assert_idle()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if any(lab.digest(source/name)!=pin for name,pin in metadata_pins.items()):
        raise RuntimeError('Accepted app configuration changed before packaging')
    shutil.copytree(source, destination)
    if any(lab.digest(source/name)!=pin or lab.digest(destination/name)!=pin for name,pin in metadata_pins.items()):
        raise RuntimeError('Accepted app configuration changed during packaging')
    signature = destination/'Contents/_CodeSignature'
    if signature.exists(): shutil.rmtree(signature)
    resources = destination/'Contents/Resources'
    shutil.copy2(icon, resources/'AppIcon.icns')
    config.update(BuildIdentifier='v'+version+'-local-release', Environment=settings, LogFile=str(log),
                  LocalDiagnostics=False, GraphicsHistoryEnabled=False)
    config.pop('GraphicsHistory', None)
    if bundle_assets:
        from bundle_assets import embed
        config=embed(destination,config)
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
    transport_metadata={'initial':prepare_signing_metadata(destination)}
    # Leaf signatures remain untouched; sign only the new shell and bundle seal.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(destination/'Contents/MacOS/LocalLauncher')], check=True)
    # Signing the main executable can create _CodeSignature metadata in its
    # enclosing bundle on exFAT; it must not become unsigned nested code.
    transport_metadata['before-bundle']=prepare_signing_metadata(destination,'before-bundle')
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(destination)], check=True)
    transport_metadata['before-verify']=prepare_signing_metadata(destination,'before-verify')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
    report = dict(schema=1, local_only=True, status='packaged_signature_verified',
        source_app=str(source), app=str(destination), title=title, version=version,
        accepted_engine_sha256=original, source_info_sha256=metadata_pins['Contents/Info.plist'],
        source_configuration_sha256=metadata_pins['Contents/Resources/RunConfiguration.plist'],
        configuration_sha256=lab.digest(resources/'RunConfiguration.plist'),
        icon_sha256=lab.digest(resources/'AppIcon.icns'),
        transport_metadata=transport_metadata,
        dependencies=({'bundled_paths':config['BundledPaths'],'persistent_storage':'Application Support/PS2 Port Lab/'+bundle_id} if bundle_assets else {'disc':config['DiscDirectory'], 'texture_pack':settings.get('PS2_PORT_TEX_PACK'), 'cd_image':config.get('CdImage')}),
        asset_storage='bundled immutable assets; saves/log/cache in per-app Application Support' if bundle_assets else 'external local project paths; app may move, project assets must remain',
        clean_rebuild_verified=False, launch_smoke_verified=False)
    lab.save(destination.with_suffix('.release.json'), report)
    return report
