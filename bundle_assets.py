"""Private self-contained game resources, with a separate persistent save root."""
from pathlib import Path
import json,re,shutil
from clean_build import path_in,sha
from asset_intake import inventory,pin


def resolve(app,configuration,support_root=None):
 """Resolve bundle-relative assets without creating directories or changing data."""
 app=Path(app).resolve();result=dict(configuration);env=dict(result.get('Environment',{}))
 mapping=result.get('BundledPaths',{})
 if not isinstance(mapping,dict) or set(mapping)-{'Executable','DiscDirectory','CdImage','PS2_PORT_TEX_PACK'}:raise ValueError('Invalid bundled asset mapping')
 resources=app/'Contents/Resources'
 for key,value in mapping.items():
  path=path_in(resources,value)
  if not path.exists():raise ValueError('Bundled asset is missing: '+key)
  if key=='PS2_PORT_TEX_PACK':env[key]=str(path)
  else:result[key]=str(path)
 if 'PortableStorage' in result and type(result['PortableStorage']) is not bool:raise ValueError('PortableStorage must be boolean')
 if result.get('PortableStorage'):
  if result['PortableStorage'] is not True or result.get('RuntimeCapabilities',{}).get('separate_save_root') is not True:raise ValueError('Portable assets require a verified separate save-root service')
  import plistlib
  info=plistlib.loads((app/'Contents/Info.plist').read_bytes());identifier=info.get('CFBundleIdentifier','')
  if not isinstance(identifier,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.-]{0,200}',identifier):raise ValueError('Unsafe portable storage identifier')
  support=Path(support_root) if support_root is not None else Path.home()/'Library/Application Support/PS2 Port Lab'
  if not support.is_absolute():raise ValueError('Portable support storage must be absolute')
  root=path_in(support.resolve(),identifier)
  env['PS2_PORT_SAVE_ROOT']=str(path_in(root,'mc0'))
  env['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR']=str(path_in(root,'cache/gpu-pipelines'))
  result['LogFile']=str(path_in(root,'logs/game.log'))
 result['Environment']=env
 return result


def _copy(root,rows,destination):
 if destination.exists() or destination.is_symlink():raise ValueError('Bundled asset destination already exists')
 destination.mkdir(parents=True)
 for row in rows:
  src=path_in(root,row['path']);dst=path_in(destination,row['path']);dst.parent.mkdir(parents=True,exist_ok=True)
  if not src.is_file() or src.stat().st_size!=row['bytes']:raise ValueError('Source asset changed')
  with src.open('rb') as incoming,dst.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
  if dst.stat().st_size!=row['bytes'] or sha(dst)!=row['sha256']:raise ValueError('Bundled bytes do not match pinned input')


def embed(app,configuration):
 """Copy only verified disc files and texture bytes; never include source saves."""
 app=Path(app).resolve();config=dict(configuration);env=dict(config.get('Environment',{}))
 if config.get('BundledPaths'):raise ValueError('Assets are already bundled')
 if config.get('RuntimeCapabilities',{}).get('separate_save_root') is not True:raise ValueError('Rebuild with the separate save-root source service before bundling assets')
 declaration=config.get('DiscManifest',{});manifest=Path(declaration.get('path',''))
 if not manifest.is_absolute() or manifest.is_symlink() or not manifest.is_file() or sha(manifest)!=declaration.get('sha256'):raise ValueError('A pinned disc manifest is required for portable packaging')
 data=json.loads(manifest.read_text());rows=data.get('files');seen=set()
 disc=Path(config['DiscDirectory']);executable=Path(config['Executable'])
 if not disc.is_absolute() or disc.is_symlink() or not disc.is_dir():raise ValueError('Invalid disc root')
 if not executable.is_absolute() or executable.is_symlink() or not executable.is_file():raise ValueError('Invalid executable')
 disc=disc.resolve();executable=executable.resolve()
 if not isinstance(rows,list) or not rows or data.get('complete') is False or data.get('expected_file_count',len(rows))!=len(rows):raise ValueError('Disc manifest is empty/incomplete')
 for row in rows:
  if not isinstance(row,dict) or row.get('status')!='copied' or type(row.get('bytes')) is not int or row['bytes']<0 or not re.fullmatch('[a-f0-9]{64}',str(row.get('sha256',''))):raise ValueError('Invalid portable disc record')
  if not isinstance(row.get('path'),str):raise ValueError('Invalid portable disc path')
  source=path_in(disc,row['path'])
  if not source.is_file() or source.stat().st_size!=row['bytes']:raise ValueError('Missing or changed portable disc source')
  name=str(source.relative_to(disc))
  if name in seen:raise ValueError('Repeated portable disc record')
  seen.add(name);row['path']=name
 rows=sorted([dict(path=row['path'],bytes=row['bytes'],sha256=row['sha256']) for row in rows],key=lambda row:row['path'])
 try:relative=str(executable.relative_to(disc))
 except ValueError:raise ValueError('Executable must belong to the pinned disc root') from None
 if relative not in seen:raise ValueError('Executable is not in the pinned disc manifest')
 resources=app/'Contents/Resources';mapping={'DiscDirectory':'RetailDisc','Executable':'RetailDisc/'+relative}
 _copy(disc,rows,path_in(resources,'RetailDisc'))
 pins={'DiscDirectory':{'sha256':pin(rows),'files':len(rows),'bytes':sum(row['bytes'] for row in rows)}}
 if env.get('PS2_PORT_TEX_PACK'):
  source=Path(env['PS2_PORT_TEX_PACK']);expected=config.get('AssetPins',{}).get('PS2_PORT_TEX_PACK',{}).get('sha256')
  root,textures=inventory(source)
  if pin(textures)!=expected:raise ValueError('Texture pack does not match accepted source pin')
  _copy(root,textures,path_in(resources,'HDTextures'));mapping['PS2_PORT_TEX_PACK']='HDTextures'
  pins['PS2_PORT_TEX_PACK']={'sha256':expected,'files':len(textures),'bytes':sum(row['bytes'] for row in textures)}
 if config.get('CdImage'):
  image=Path(config['CdImage'])
  if not image.is_absolute() or image.is_symlink() or not image.is_file():raise ValueError('Unsafe sector image')
  expected=sha(image);target=path_in(resources,'Disc.iso');shutil.copyfile(image,target)
  if sha(target)!=expected:raise ValueError('Sector image changed during bundling')
  mapping['CdImage']='Disc.iso';pins['CdImage']={'sha256':expected,'bytes':target.stat().st_size}
 if sha(manifest)!=declaration['sha256']:raise ValueError('Disc manifest changed during bundling')
 for key in ('Executable','DiscDirectory','CdImage','DiscManifest','AssetPins'):config.pop(key,None)
 # A portable package owns its writable root. Never carry an older runner's
 # external memory-card override into the new per-app storage configuration.
 for key in ('PS2_PORT_TEX_PACK','PS2_PORT_SAVE_ROOT','PS2_PORT_MC_ROOT','PS2_PORT_HW_GS_PIPELINE_CACHE_DIR'):env.pop(key,None)
 config.update(Environment=env,BundledPaths=mapping,BundledAssetPins=pins,PortableStorage=True)
 return config
