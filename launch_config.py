"""Declarative native launch settings, with local asset and framework provenance.

No shell environment is a release recipe. Settings are explicit and checked
before packaging; runtime library paths are resolved from the moved app bundle.
"""
from pathlib import Path
import hashlib,json,re
from clean_build import path_in,sha,filesystem_metadata

BOOLS={'PS2_PORT_HW_GS_SURFACE_METRICS','PS2_PORT_HW_GS_ZERO_COPY','PS2_PORT_EE64_VARARGS','PS2_PORT_HW_GS_ALPHA_CUT','PS2_PORT_HW_GS_TEXTURE_HWFILTER','PS2_PORT_AUDIO_LEGACY','PS2_PORT_NATIVE_PCM','PS2_PORT_KEY_EVENTS','PS2_PORT_CAMERA_STEP_FIX','PS2_PORT_PIER_FIX','PS2_PORT_WORLD_BUBBLES','PS2_PORT_HW_GS_BLOB_PREFETCH','PS2_PORT_HW_GS_RESIDENCY_CACHE','PS2_PORT_HW_GS_RUNTIME_MIPS','PS2_PORT_HW_GS_SCANOUT_RECT','PS2_PORT_TEX_PACK_T8_COMPOUND','PS2_PORT_PRIVATE_SDR_GUEST_ONLY','PS2_PORT_PRIVATE_SS_TEXTURES','PS2_PORT_PRIVATE_WINDOW_CONTROL'}
BOOLS|={'PS2_PORT_ENHANCE_'+n for n in ['ASYNC_GPU','CLIP','FXAA','HIRES_SCANOUT','PROGRESSIVE','SSAA_DYNAMIC','VSYNC','WIDESCREEN','WIDESCREEN_HUD']}
NUMBERS={'PS2_PORT_ENHANCE_'+n for n in ['FOV','IOP_BATCH','MOVIE_WEAVE','PACING_MS','SHARPNESS','SSAA','SSAA_DROP_MS','SSAA_PANIC_MS','UPSCALE']}
NUMBERS|={'PS2_PORT_HW_GS_SCALE','PS2_PORT_PRIVATE_IOP_IMPORT_CYCLES'}
BINDINGS={'PS2_PORT_PRIVATE_GS_PLUGIN','PS2_PORT_PRIVATE_MOLTENVK'}
BOOLS|={'PS2_PORT_SPECIAL_TIMER_RESET','PS2_PORT_SPECIAL_TIMER_PER_SPECIAL'}
BOOLS|={'PS2_PORT_HW_GS_ADAPTIVE','PS2_PORT_HW_GS_STALE_TARGET_FIX'}
PATHS={'PS2_PORT_TEX_PACK','PS2_PORT_HW_GS_PIPELINE_CACHE_DIR','PS2_PORT_SAVE_ROOT'}


def directory_pin(directory):
 raw=Path(directory)
 if raw.is_symlink():raise ValueError('Native launch asset directory may not be a symlink')
 root=raw.resolve();rows=[]
 if not root.is_dir():raise ValueError('Native launch asset directory missing')
 for p in sorted(root.rglob('*')):
  if p.is_symlink():raise ValueError('Native launch assets may not contain symlinks')
  if p.is_file() and filesystem_metadata(p):continue
  if p.is_file():rows.append(dict(path=str(p.relative_to(root)),sha256=sha(p),bytes=p.stat().st_size))
  if len(rows)>100000:raise ValueError('Native launch asset inventory exceeds bound')
 if not rows:raise ValueError('Native launch asset directory is empty')
 return dict(sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),files=len(rows),bytes=sum(x['bytes'] for x in rows))


def prepare(profile,private,frameworks):
 private=Path(private).resolve();spec=profile.get('native_launch',{})
 if not isinstance(spec,dict) or set(spec)-{'environment','paths','framework_bindings','renderer_packet_batch','renderer_deinterlace','diagnostics'}:raise ValueError('Unknown native launch configuration field')
 env=spec.get('environment',{});paths=spec.get('paths',{});bindings=spec.get('framework_bindings',{})
 if not all(isinstance(x,dict) for x in [env,paths,bindings]):raise ValueError('Native launch settings must be mappings')
 result={}
 for key,value in env.items():
  if not isinstance(value,str) or not value or len(value)>100 or '\0' in value or '\n' in value:raise ValueError('Native launch values must be bounded strings')
  valid=(key in BOOLS and value in {'0','1'}) or (key in NUMBERS and bool(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value)))
  valid|=key=='PS2_PORT_ENHANCE_MOVIE_FILL' and value in {'off','on','0','1'}
  valid|=key=='PS2_PORT_ENHANCE_WINSIZE' and bool(re.fullmatch(r'[1-9][0-9]{1,4}x[1-9][0-9]{1,4}',value))
  valid|=key=='PS2_PORT_HW_GS_ADAPTIVE_STALLS' and bool(re.fullmatch(r'[1-9][0-9]{0,5}',value)) and int(value)<=100000
  if not valid:raise ValueError('Unknown or invalid native launch setting: '+str(key))
  result[key]=value
 assets={}
 for key,record in paths.items():
  if key not in PATHS or not isinstance(record,dict) or set(record)-{'path','sha256','source'}:raise ValueError('Unknown native launch path')
  path=path_in(private,record.get('path',''))
  if key=='PS2_PORT_TEX_PACK':
   if 'source' in record:
    from asset_intake import stage
    stage(record['source'],private,record['path'],record.get('sha256'))
   pin=directory_pin(path)
   if pin['sha256']!=record.get('sha256'):raise ValueError('Native launch texture pack differs from profile pin')
   assets[key]=dict(path=str(path),**pin)
  elif 'source' in record or 'sha256' in record:raise ValueError('Mutable cache directory may not masquerade as pinned assets')
  result[key]=str(path)
 for key,name in bindings.items():
  if key not in BINDINGS or not isinstance(name,str) or name not in frameworks:raise ValueError('Native launch library must name a source-built bundled framework')
 if result.get('PS2_PORT_HW_GS_ZERO_COPY')=='1' and (result.get('PS2_PORT_ENHANCE_HIRES_SCANOUT')!='1' or 'PS2_PORT_PRIVATE_GS_PLUGIN' not in bindings):
  raise ValueError('Surface transport requires packed HD scanout and a source-built plugin binding')
 if 'PS2_PORT_PRIVATE_GS_PLUGIN' in bindings and 'PS2_PORT_HW_GS_PIPELINE_CACHE_DIR' not in result:
  # The source-built adapter's host settings stub has no SettingsPath.
  # Always give its renderer a per-game mutable cache location.
  result['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR']=str(path_in(private,'cache/gpu-pipelines'))
 batch=spec.get('renderer_packet_batch',False);deinterlace=spec.get('renderer_deinterlace',1);diagnostics=spec.get('diagnostics',True)
 if type(batch) is not bool or type(diagnostics) is not bool or type(deinterlace) is not int or deinterlace not in {0,1,2}:raise ValueError('Invalid renderer launch controls')
 return dict(Environment=result,FrameworkEnvironment=bindings,RendererPacketBatch=batch,RendererDeinterlace=deinterlace,LocalDiagnostics=diagnostics,assets=assets)


def execution_environment(profile,prepared,frameworks,private):
 """Mirror declared launcher controls for a direct, isolated native probe."""
 result=dict(prepared['Environment'])
 for field,key in [('renderer_worker','PS2_PORT_GS_THREAD'),('async_presentation','PS2_PORT_ASYNC_PRESENT')]:
  value=profile.get(field,False)
  if type(value) is not bool:raise ValueError('Profile '+field+' must be a boolean')
  result[key]='1' if value else '0'
 result['PS2_PORT_PRIVATE_GIF_BATCH']='1' if prepared['RendererPacketBatch'] else '0'
 result['PS2_PORT_PRIVATE_DEINTERLACE']=str(prepared['RendererDeinterlace'])
 for key,name in prepared['FrameworkEnvironment'].items():
  if name not in frameworks:raise ValueError('Probe renderer library is not in the verified build')
  result[key]=str(Path(frameworks[name]).resolve())
 if prepared['LocalDiagnostics']:
  result['PS2_PORT_GS_PROFILE']='1';result['PS2_PORT_PAD_TRACE']='1'
 history=profile.get('graphics_history',False)
 if type(history) is not bool:raise ValueError('Profile graphics_history must be a boolean')
 if history:
  result.update(PS2_PORT_GS_TRACE='1',PS2_PORT_VU_TRACE='1',
                PS2_PORT_GS_HISTORY=str(Path(private).resolve()/'logs/window-graphics-history.jsonl'))
 return result
