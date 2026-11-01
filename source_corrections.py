"""Retain and replay local source corrections by executable and source identity.

Records contain private replacement source. They are data, never Python/shell,
and are not evidence of semantic equivalence or gameplay qualification.
"""
from pathlib import Path
import hashlib,json,re
from clean_build import inspect,sha,path_in,filesystem_metadata
from graph_amend import amend


def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def fingerprint(value):return hashlib.sha256(encoded(value)).hexdigest()
def topology(recipe):return fingerprint({key:recipe[key] for key in ('units','targets')})


def capture(executable,before_recipe,staging,after_recipe,configuration,*,bank=None):
 """Capture every changed source file between two pinned, same-topology graphs."""
 before_recipe=Path(before_recipe).resolve();after_recipe=Path(after_recipe).resolve();configuration=Path(configuration).resolve()
 before=inspect(before_recipe);after=inspect(after_recipe)
 if topology(before['recipe'])!=topology(after['recipe']) or set(before['recipe']['files'])!=set(after['recipe']['files']):raise ValueError('Correction capture needs identical source inventory and build topology')
 if configuration.exists() or any(configuration.is_relative_to(g['root']) for g in (before,after)):raise ValueError('Use a new configuration outside both source graphs')
 edits=[]
 for name,pin in before['recipe']['files'].items():
  if pin==after['recipe']['files'][name]:continue
  raw=(before['root']/name).read_bytes();new=(after['root']/name).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=pin or hashlib.sha256(new).hexdigest()!=after['recipe']['files'][name]:raise ValueError('Source changed during correction capture')
  try:old=raw.decode('utf8');replacement=new.decode('utf8')
  except UnicodeDecodeError:old=raw.decode('latin1');replacement=new.decode('latin1')
  # Replay uses the original source's encoding. Do not silently transcode bytes.
  try:encoding='utf8';raw.decode(encoding)
  except UnicodeDecodeError:encoding='latin1'
  if replacement.encode(encoding)!=new:raise ValueError('Correction changes source encoding; declare that change separately')
  if not old:raise ValueError('Correction capture cannot replace an empty source file')
  edits.append(dict(file=name,sha256=pin,before=old,after=replacement,reason='Captured changed source from pinned graph '+after['recipe_sha256']))
 if not edits:raise ValueError('No changed source files to retain')
 executable=Path(executable).resolve();staging=Path(staging).resolve()
 config=dict(schema=1,recipe=str(before_recipe),recipe_sha256=before['recipe_sha256'],staging=str(staging),staging_sha256=sha(staging),
  title_revision=dict(executable=str(executable),sha256=sha(executable)),source_provenance=dict(path=str(after_recipe),sha256=after['recipe_sha256']),edits=edits,
  source_contracts=after['recipe'].get('source_contracts',[]))
 configuration.parent.mkdir(parents=True,exist_ok=True)
 with configuration.open('xb') as stream:stream.write(encoded(config))
 configuration.chmod(0o600)
 amend(configuration,None,validate_only=True)
 if inspect(after_recipe)['recipe_sha256']!=after['recipe_sha256']:raise ValueError('Captured source graph changed')
 report=dict(status='source_corrections_captured_not_built',configuration=str(configuration),sha256=sha(configuration),changed_files=[e['file'] for e in edits],gameplay_verified=False)
 if bank is not None:report['retained']=retain(configuration,bank)
 return report


def retain(config_path,bank):
 config_path=Path(config_path).resolve();validated=amend(config_path,None,validate_only=True)
 config=json.loads(config_path.read_text())
 if sha(config_path)!=validated['config_sha256']:raise ValueError('Correction configuration changed')
 if validated['title_revision'] is None:raise ValueError('Retained corrections require an executable revision')
 recipe=inspect(config['recipe'])
 if recipe['recipe_sha256']!=config['recipe_sha256']:raise ValueError('Correction source graph changed')
 record=dict(schema=1,kind='revision_source_corrections',executable_sha256=validated['title_revision']['sha256'],
  topology_sha256=topology(recipe['recipe']),edits=config['edits'],origin_config_sha256=validated['config_sha256'])
 if 'source_contracts' in config:record['source_contracts']=config['source_contracts']
 if validated['source_provenance']:record['source_provenance']=validated['source_provenance']
 bank=Path(bank).resolve();bank.mkdir(parents=True,exist_ok=True,mode=0o700)
 key=fingerprint(record);target=bank/(key+'.json');raw=encoded(record)
 if target.exists():
  if target.is_symlink() or target.read_bytes()!=raw:raise ValueError('Retained correction record changed')
 else:
  with target.open('xb') as f:f.write(raw)
  target.chmod(0o600)
 from launch_config import directory_pin
 return dict(status='private_source_corrections_retained',id=key,record=str(target),bank_sha256=directory_pin(bank)['sha256'],gameplay_verified=False)


def candidates(bank,executable,recipe_path):
 executable=Path(executable).resolve();identity=sha(executable);checked=inspect(recipe_path);recipe=checked['recipe'];result=[]
 bank=Path(bank).resolve()
 for path in sorted(bank.glob('*.json')):
  if path.is_file() and filesystem_metadata(path):continue
  if path.is_symlink() or not re.fullmatch('[a-f0-9]{64}',path.stem):raise ValueError('Invalid correction bank entry')
  record=json.loads(path.read_text())
  if fingerprint(record)!=path.stem or record.get('schema')!=1 or record.get('kind')!='revision_source_corrections':raise ValueError('Correction bank record pin changed')
  if record.get('executable_sha256')!=identity:continue
  blockers=[]
  if record.get('topology_sha256')!=topology(recipe):blockers.append('source_graph_topology_changed')
  edits=record.get('edits')
  if not isinstance(edits,list) or not edits:raise ValueError('Invalid retained source edits')
  for edit in edits:
   name=edit.get('file')
   if not isinstance(name,str) or recipe['files'].get(name)!=edit.get('sha256'):blockers.append('source_pin_changed:'+str(name))
  result.append(dict(id=path.stem,ready=not blockers,blockers=blockers,record=record))
 return dict(executable_sha256=identity,recipe_sha256=checked['recipe_sha256'],candidates=result)


def apply(bank,executable,recipe_path,staging_path,destination):
 found=candidates(bank,executable,recipe_path);ready=[r for r in found['candidates'] if r['ready']]
 if len(ready)!=1:raise ValueError('Expected exactly one matching source correction set; found '+str(len(ready)))
 record=ready[0]['record'];recipe_path=Path(recipe_path).resolve();staging_path=Path(staging_path).resolve();out=Path(destination).resolve()
 root=recipe_path.parent
 if out.is_relative_to(root) or root.is_relative_to(out):raise ValueError('Correction output overlaps the input source graph')
 config=dict(schema=1,recipe=str(recipe_path),recipe_sha256=found['recipe_sha256'],staging=str(staging_path),staging_sha256=sha(staging_path),
  title_revision=dict(executable=str(Path(executable).resolve()),sha256=found['executable_sha256']),edits=record['edits'])
 if 'source_contracts' in record:config['source_contracts']=record['source_contracts']
 # Retained source text is sufficient for replay; the original donor path may
 # no longer exist. Its provenance stays in the immutable bank record.
 manifest=out.with_name(out.name+'.correction.json')
 if out.exists() or manifest.exists():raise ValueError('Use a fresh correction output and manifest')
 out.parent.mkdir(parents=True,exist_ok=True)
 with manifest.open('xb') as stream:stream.write(encoded(config))
 manifest.chmod(0o600)
 report=amend(manifest,out)
 report.update(correction_id=ready[0]['id'],configuration=str(manifest),configuration_sha256=sha(manifest),automatic_selection='exact_executable_source_and_topology',gameplay_verified=False)
 return report


def verify_native_receipt(entry,private):
 """Recheck correction inputs and staged source when resuming/packaging."""
 from launch_config import directory_pin
 if not isinstance(entry,dict) or set(entry)!={'path','sha256'} or not isinstance(entry['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):raise ValueError('Invalid native correction receipt declaration')
 private=Path(private).resolve();path=path_in(private,entry['path'])
 if sha(path)!=entry['sha256']:raise ValueError('Native correction receipt changed')
 receipt=json.loads(path.read_text());contract=receipt['contract']
 bank=path_in(private,contract['bank'])
 if directory_pin(bank)['sha256']!=contract['bank_sha256']:raise ValueError('Native correction bank changed')
 for key in ['recipe','staging','executable']:
  if sha(path_in(private,contract[key]))!=contract[key+'_sha256']:raise ValueError('Native correction '+key+' changed')
 for name,pin in receipt['pinned_files'].items():
  if sha(path_in(private,name))!=pin:raise ValueError('Native correction input/output changed: '+name)
 graph=inspect(path_in(private,receipt['graph']['recipe']))
 if graph['recipe_sha256']!=receipt['graph']['sha256']:raise ValueError('Corrected source graph changed')
 return receipt


def preserve_interrupted_preparation(folder,contract):
 """Move an identified unfinished cache aside; never overwrite or delete it."""
 import uuid
 folder=Path(folder)
 if folder.is_symlink():raise ValueError('Unsafe interrupted correction directory')
 if not folder.exists():return None
 receipt=folder/'receipt.json'
 if receipt.is_symlink():raise ValueError('Unsafe correction receipt')
 if receipt.exists():return None
 manifest=folder/'graph.correction.json'
 if manifest.is_symlink():raise ValueError('Unsafe interrupted correction manifest')
 if manifest.exists():
  data=json.loads(manifest.read_text())
  expected={'recipe_sha256':contract['recipe_sha256'],'staging_sha256':contract['staging_sha256']}
  if data.get('schema')!=1 or any(data.get(k)!=v for k,v in expected.items()) or data.get('title_revision',{}).get('sha256')!=contract['executable_sha256']:
   raise ValueError('Unrecognized incomplete source preparation; preserve and inspect it')
 elif any(folder.iterdir()):raise ValueError('Incomplete source preparation lacks its identity manifest')
 backup=folder.with_name(folder.name+'.interrupted-'+uuid.uuid4().hex)
 if backup.exists():raise ValueError('Interrupted source backup already exists')
 folder.rename(backup)
 return str(backup)

def prepare_native(profile,private):
 """Use a profile's declared bank automatically before a native source build."""
 from launch_config import directory_pin
 from pipeline import boot_executable
 private=Path(private).resolve();spec=profile['native_source_graph'];declared=spec.get('corrections')
 if not isinstance(declared,dict) or set(declared)!={'bank','sha256'}:raise ValueError('Declare a pinned private correction bank')
 bank=path_in(private,declared['bank'])
 if directory_pin(bank)['sha256']!=declared['sha256']:raise ValueError('Native correction bank differs from profile pin')
 executable=boot_executable(private/'disc-root',profile);identity=sha(executable)
 if identity!=profile.get('expected_sha256'):raise ValueError('Native corrections require the actual pinned executable revision')
 recipe=path_in(private,spec['recipe']);staging=path_in(private,spec['staging'])
 if sha(recipe)!=spec['sha256'] or sha(staging)!=spec['staging_sha256']:raise ValueError('Native correction source pins changed')
 inspect(recipe) # A cached derived graph must not hide changed/missing base files.
 contract=dict(bank=declared['bank'],bank_sha256=declared['sha256'],executable_sha256=identity,
  recipe=spec['recipe'],staging=spec['staging'],executable=str(executable.resolve().relative_to(private)),
  recipe_sha256=spec['sha256'],staging_sha256=spec['staging_sha256'],implementation={p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('graph_amend.py')]})
 folder=path_in(private,'analysis/source-corrections/'+fingerprint(contract));receipt_path=folder/'receipt.json'
 if receipt_path.exists():
  entry=dict(path=str(receipt_path.relative_to(private)),sha256=sha(receipt_path));receipt=verify_native_receipt(entry,private)
  if receipt['contract']!=contract:raise ValueError('Native correction preparation contract changed')
 else:
  interrupted=preserve_interrupted_preparation(folder,contract)
  result=apply(bank,executable,recipe,staging,folder/'graph')
  corrected=Path(result['recipe']);ledger=Path(result['staging']);configuration=Path(result['configuration'])
  graph=dict(recipe=str(corrected.relative_to(private)),sha256=sha(corrected),staging=str(ledger.relative_to(private)),staging_sha256=sha(ledger))
  pins={str(p.relative_to(private)):sha(p) for p in [recipe,staging,corrected,ledger,configuration,executable]}
  receipt=dict(schema=1,status='native_source_corrections_prepared_not_built',contract=contract,graph=graph,correction_id=result['correction_id'],pinned_files=pins,gameplay_verified=False)
  if interrupted:receipt['preserved_interrupted_preparation']=interrupted
  receipt_path.write_bytes(encoded(receipt));receipt_path.chmod(0o600)
  entry=dict(path=str(receipt_path.relative_to(private)),sha256=sha(receipt_path))
  verify_native_receipt(entry,private)
 effective=dict(profile);updated=dict(spec,**receipt['graph']);updated.pop('corrections',None)
 # Explicit generated-source substitutions must follow the corrected file's
 # pinned bytes. Original generated/disc identity is still checked downstream.
 if spec.get('generated_substitutions'):
  files=inspect(path_in(private,updated['recipe']))['recipe']['files']
  updated['generated_substitutions']=[dict(row,staged_sha256=files[row['staged']]) for row in spec['generated_substitutions']]
 effective['native_source_graph']=updated
 return effective,entry
