"""Explicit local platform dependencies, never historical game object inputs."""
from pathlib import Path
import re,subprocess
from clean_build import path_in,sha

KINDS={'moltenvk':dict(framework='libMoltenVK.dylib',origin='https://github.com/KhronosGroup/MoltenVK')}


def inspect_library(path):
 """Inspect without loading/running the SDK. Require a native ARM64 dylib."""
 headers=subprocess.check_output(['/usr/bin/otool','-arch','arm64','-hv',str(path)],text=True,stderr=subprocess.STDOUT,timeout=15)
 if not any('ARM64' in row.split() and 'DYLIB' in row.split() for row in headers.splitlines()):raise ValueError('Platform SDK needs an ARM64 dynamic library')
 from native_components import binary_dependencies
 return binary_dependencies(path)


def verify(artifact,private):
 private=Path(private).resolve();declaration=artifact.get('platform_sdk')
 if not isinstance(declaration,dict) or set(declaration)!={'kind','origin','source_build_verified'} or declaration.get('kind') not in KINDS:raise ValueError('Invalid platform SDK provenance')
 kind=KINDS[declaration['kind']]
 if declaration['origin']!=kind['origin'] or declaration['source_build_verified'] is not False:raise ValueError('Platform SDK provenance must not claim a source build')
 path=Path(artifact.get('path',''))
 if not path.is_absolute() or not path.is_relative_to(private):raise ValueError('Platform SDK must be staged in private game storage')
 path=path_in(private,str(path.relative_to(private)))
 if path.name!=kind['framework'] or not path.is_file() or not isinstance(artifact.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',artifact['sha256']) or sha(path)!=artifact['sha256']:raise ValueError('Platform SDK file differs from its pin')
 inspect_library(path)
 if sha(path)!=artifact['sha256']:raise ValueError('Platform SDK changed during inspection')
 return artifact


def frameworks(spec,private):
 entries=spec.get('platform_sdks',[])
 if not isinstance(entries,list):raise ValueError('Platform SDK declarations must be a list')
 result={};private=Path(private).resolve()
 for entry in entries:
  if not isinstance(entry,dict) or set(entry)!={'kind','path','sha256'} or entry.get('kind') not in KINDS:raise ValueError('Unknown platform SDK declaration')
  kind=KINDS[entry['kind']];name=kind['framework']
  if name in result:raise ValueError('Duplicate platform SDK declaration')
  path=path_in(private,entry['path'])
  artifact=dict(path=str(path),sha256=entry['sha256'],platform_sdk=dict(kind=entry['kind'],origin=kind['origin'],source_build_verified=False))
  result[name]=verify(artifact,private)
 return result
