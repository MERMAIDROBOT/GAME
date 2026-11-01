"""Apply explicit, pinned source corrections to a new graph; never edit inputs.

Literal edits are data, never shell commands. Every edit requires one exact
match, the previous file hash and a reason. No historical object is copied.
"""
from pathlib import Path
import json,shutil,re
from clean_build import inspect,sha,path_in,validate_source_contracts


def pinned_input(entry,label,path_key):
 if not isinstance(entry,dict):raise ValueError(label+' must declare a pinned input')
 path=entry.get(path_key);pin=entry.get('sha256')
 if not isinstance(path,str) or not Path(path).is_absolute() or not isinstance(pin,str) or not re.fullmatch('[a-f0-9]{64}',pin):raise ValueError(label+' needs an absolute path and SHA-256')
 source=Path(path)
 if source.is_symlink() or not source.is_file() or sha(source)!=pin:raise ValueError(label+' input pin changed')
 return {path_key:str(source.resolve()),'sha256':pin}


def amend(config_path,destination,*,validate_only=False):
 config_path=Path(config_path).resolve();config_pin=sha(config_path);config=json.loads(config_path.read_text())
 if config.get('schema')!=1 or not isinstance(config.get('edits'),list) or not config['edits']:raise ValueError('Declare pinned source edits')
 revision=pinned_input(config['title_revision'],'Title revision','executable') if 'title_revision' in config else None
 provenance=pinned_input(config['source_provenance'],'Source provenance','path') if 'source_provenance' in config else None
 recipe_path=Path(config['recipe']).resolve();ledger_path=Path(config['staging']).resolve()
 if sha(recipe_path)!=config.get('recipe_sha256') or sha(ledger_path)!=config.get('staging_sha256'):raise ValueError('Source graph or ledger pin changed')
 checked=inspect(recipe_path);root=checked['root'];recipe=checked['recipe'];ledger=json.loads(ledger_path.read_text())
 if ledger.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Ledger covers a different source graph')
 rows={r['staged']:r for r in ledger.get('files',[])}
 if any(rows.get(p,{}).get('sha256')!=pin for p,pin in recipe['files'].items()):raise ValueError('Ledger must cover all pinned source files')
 out=None if validate_only else Path(destination).resolve()
 if out is not None and (out.exists() or out.is_relative_to(root) or root.is_relative_to(out)):raise ValueError('Use a fresh nonoverlapping amended graph directory')
 edits={}
 for entry in config['edits']:
  name=entry.get('file');old=entry.get('before');new=entry.get('after');reason=entry.get('reason')
  if name not in recipe['files'] or name in edits:raise ValueError('Edit must name one unique pinned source file')
  if not isinstance(old,str) or not old or not isinstance(new,str) or old==new or not isinstance(reason,str) or not reason.strip():raise ValueError('Edit needs literal before/after and a reason')
  source=path_in(root,name)
  if sha(source)!=entry.get('sha256'):raise ValueError('Edited source pin changed')
  # Decode losslessly; translation inventories may contain Latin-1 comments.
  raw=source.read_bytes()
  try:text=raw.decode('utf8');encoding='utf8'
  except UnicodeDecodeError:text=raw.decode('latin1');encoding='latin1'
  if text.count(old)!=1:raise ValueError('Source edit must match exactly once')
  edits[name]=(text.replace(old,new).encode(encoding),entry)
 # Validate the resulting implementation before copying thousands of files.
 planned=dict(recipe)
 if 'source_contracts' in config:
  contracts=config['source_contracts']
  if not isinstance(contracts,list) or any(not isinstance(c,dict) for c in contracts):raise ValueError('Invalid replacement source contracts')
  if not {c['id'] for c in recipe.get('source_contracts',[])}<={c.get('id') for c in contracts}:raise ValueError('Amendment may not drop source contract IDs')
  planned['source_contracts']=contracts
 def planned_source(name):
  data=edits[name][0] if name in edits else path_in(root,name).read_bytes()
  return data.decode('utf8',errors='surrogateescape')
 validate_source_contracts(planned,planned_source)
 if validate_only:return dict(status='source_amendment_inputs_validated',config_sha256=config_pin,title_revision=revision,source_provenance=provenance,equivalence_verified=False)
 out.mkdir(parents=True)
 try:
  for name,pin in recipe['files'].items():
   src=path_in(root,name);dst=out/name;dst.parent.mkdir(parents=True,exist_ok=True)
   shutil.copyfile(src,dst)
   if sha(src)!=pin or sha(dst)!=pin:raise ValueError('Source changed during amendment')
  for name,(data,entry) in edits.items():
   (out/name).write_bytes(data);pin=sha(out/name)
   rows[name].setdefault('source_amendments',[]).append(dict(previous_sha256=recipe['files'][name],sha256=pin,reason=entry['reason'],config_sha256=config_pin))
   rows[name]['sha256']=pin;recipe['files'][name]=pin
  # Include directories can be empty; retain their declared topology.
  for unit in recipe['units']:
   for name in unit.get('includes',[]):(out/name).mkdir(parents=True,exist_ok=True)
   for item in unit.get('include_paths',[]):(out/item['path']).mkdir(parents=True,exist_ok=True)
  if 'source_contracts' in config:
   contracts=config['source_contracts']
   if not isinstance(contracts,list) or any(not isinstance(c,dict) for c in contracts):raise ValueError('Invalid replacement source contracts')
   old_ids={c['id'] for c in recipe.get('source_contracts',[])}
   if not old_ids<={c.get('id') for c in contracts}:raise ValueError('Amendment may not drop source contract IDs')
   recipe['source_contracts']=contracts
  result=out/'recipe.json';result.write_text(json.dumps(recipe,indent=2)+'\n');inspect(result)
  if sha(config_path)!=config_pin or sha(recipe_path)!=config['recipe_sha256'] or sha(ledger_path)!=config['staging_sha256']:raise ValueError('Amendment inputs changed')
  inspect(recipe_path)
  if revision:pinned_input(revision,'Title revision','executable')
  if provenance:pinned_input(provenance,'Source provenance','path')
  ledger.update(status='source_graph_amended_not_built',recipe_sha256=sha(result),dependency_closure_verified=False,equivalence_verified=False,gameplay_verified=False)
  amendment=dict(config_sha256=config_pin,previous_recipe_sha256=config['recipe_sha256'],previous_staging_sha256=config['staging_sha256'],files=list(edits))
  if revision:amendment['title_revision']=revision
  if provenance:amendment['source_provenance']=provenance
  ledger.setdefault('source_amendments',[]).append(amendment)
  (out/'staging.json').write_text(json.dumps(ledger,indent=2)+'\n')
  return dict(status=ledger['status'],recipe=str(result),recipe_sha256=sha(result),staging=str(out/'staging.json'),changed_files=list(edits),historical_objects_copied=False,equivalence_verified=False)
 except Exception as error:
  (out/'failed.json').write_text(json.dumps(dict(status='failed',error=str(error)),indent=2)+'\n');raise
