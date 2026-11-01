"""Combine independently staged source graphs into one isolated build root.

Copies only pinned source inventories, preserves relative includes and ledgers,
and qualifies neither source equivalence nor any game behavior.
"""
from pathlib import Path
import hashlib,json,shutil
from clean_build import inspect,sha,ID,path_in


def merge(config_path,destination,*,resume=False):
 config_path=Path(config_path).resolve();raw=config_path.read_bytes();config=json.loads(raw)
 if config.get('schema')!=1 or not isinstance(config.get('graphs'),list) or not config['graphs']:raise ValueError('Declare schema 1 source graphs')
 out=Path(destination).resolve()
 previous_failure=None
 if out.exists():
  if not resume or not (out/'failed.json').is_file():raise ValueError('Use a new merged source directory or explicitly resume a failed source merge')
  previous_failure=sha(out/'failed.json')
 elif resume:raise ValueError('No failed source merge to resume')
 inputs=[];names=set()
 for item in config['graphs']:
  name=item.get('id','')
  if not ID.fullmatch(name) or name.casefold() in names:raise ValueError('Invalid/duplicate source graph id')
  names.add(name.casefold());recipe=Path(item['recipe']).resolve();ledger_path=Path(item['staging']).resolve()
  if sha(recipe)!=item.get('recipe_sha256') or sha(ledger_path)!=item.get('staging_sha256'):raise ValueError('Input source graph/ledger pin differs')
  checked=inspect(recipe);ledger=json.loads(ledger_path.read_text())
  if ledger.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Input staging ledger covers a different recipe')
  if out.is_relative_to(checked['root']) or checked['root'].is_relative_to(out):raise ValueError('Merged output overlaps source inputs')
  by_path={f['staged']:f for f in ledger.get('files',[])}
  if any(by_path.get(p,{}).get('sha256')!=digest for p,digest in checked['recipe']['files'].items()):raise ValueError('Source ledger does not cover every pinned input')
  inputs.append((name,item,checked,by_path))
 recipe=dict(schema=1,source_only=True,files={},units=[],targets=[]);records=[];origins=[];amendments=[]
 out.mkdir(parents=True,exist_ok=resume)
 if previous_failure:
  history=out/('failed-before-resume-'+previous_failure+'.json')
  if not history.exists():shutil.copyfile(out/'failed.json',history)
 progress=dict(schema=1,phase='copying',processed_files=0,total_files=sum(len(c['recipe']['files']) for _,_,c,_ in inputs),copied_files=0,resumed=resume)
 def update(**values):
  progress.update(values);temporary=out/'progress.next.json';temporary.write_text(json.dumps(progress,indent=2)+'\n');temporary.replace(out/'progress.json')
 update()
 try:
  for name,item,checked,by_path in inputs:
   update(graph=name)
   prefix=Path(name);root=checked['root'];graph=checked['recipe']
   def renamed(value):return str(prefix/Path(value))
   for path,digest in graph['files'].items():
    source=path_in(root,path);dest=out/renamed(path);dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
     if not resume or dest.is_symlink() or not dest.is_file() or sha(dest)!=digest:raise ValueError('Existing resumed source differs from its pin')
    else:shutil.copyfile(source,dest);progress['copied_files']+=1
    if sha(dest)!=digest or sha(source)!=digest:raise ValueError('Source changed while merging')
    recipe['files'][renamed(path)]=digest
    records.append(dict(by_path[path],staged=renamed(path),source_graph=name))
    progress['processed_files']+=1
    if progress['processed_files']%256==0:update()
   for unit in graph['units']:
    u=dict(unit,id=name+'.'+unit['id'],source=renamed(unit['source']))
    u['includes']=[renamed(p) for p in unit.get('includes',[])]
    u['include_paths']=[dict(kind=x['kind'],path=renamed(x['path'])) for x in unit.get('include_paths',[])]
    u['force_include']=[renamed(p) for p in unit.get('force_include',[])]
    for p in [*u['includes'],*(x['path'] for x in u['include_paths'])]:(out/p).mkdir(parents=True,exist_ok=True)
    recipe['units'].append(u)
   for target in graph['targets']:
    recipe['targets'].append(dict(target,id=name+'.'+target['id'],units=[name+'.'+n for n in target.get('units',[])],uses=[name+'.'+n for n in target.get('uses',[])]))
   for contract in graph.get('source_contracts',[]):
    carried=dict(contract,id=name+'.'+contract['id'],unit=name+'.'+contract['unit'],target=name+'.'+contract['target'])
    if 'file' in carried:carried['file']=renamed(carried['file'])
    if 'header_reference' in carried:carried['header_reference']=renamed(carried['header_reference'])
    recipe.setdefault('source_contracts',[]).append(carried)
   origins.append(dict(id=name,recipe_sha256=item['recipe_sha256'],staging_sha256=item['staging_sha256']))
   ledger=json.loads(Path(item['staging']).read_text())
   for amendment in ledger.get('source_amendments',[]):
    carried=dict(amendment,source_graph=name,files=[renamed(p) for p in amendment['files']])
    amendments.append(carried)
  recipe['targets']+=config.get('targets',[])
  from source_graph import reconcile_owned_audio_headers
  reconcile_owned_audio_headers(out,recipe,records)
  target=out/'recipe.json';target.write_text(json.dumps(recipe,indent=2)+'\n');update(phase='validating_merged_graph');checked=inspect(target)
  if hashlib.sha256(raw).hexdigest()!=sha(config_path):raise ValueError('Merge configuration changed')
  update(phase='checking_input_pins')
  for name,item,old,by_path in inputs:
   if sha(item['recipe'])!=item['recipe_sha256'] or sha(item['staging'])!=item['staging_sha256']:raise ValueError('Source graph changed while merging')
  report=dict(schema=1,status='source_graphs_merged_not_built',recipe_sha256=checked['recipe_sha256'],config_sha256=sha(config_path),graphs=origins,files=records,
   source_files=len(records),units=len(recipe['units']),historical_objects_copied=False,dependency_closure_verified=False,equivalence_verified=False,gameplay_verified=False)
  if amendments:report['source_amendments']=amendments
  if previous_failure:report['resumed_failed_receipt_sha256']=previous_failure
  (out/'staging.json').write_text(json.dumps(report,indent=2)+'\n');update(phase='source_graphs_merged_not_built');return report
 except Exception as error:
  (out/'failed.json').write_text(json.dumps(dict(status='failed',error=str(error)),indent=2)+'\n');update(phase='failed',error=str(error));raise
