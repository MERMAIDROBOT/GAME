"""Stage declared source roots into a relocatable, hash-pinned build graph.

Copies source, never objects. Absolute includes must resolve within declared
roots and are rewritten to relative staged includes. No historical scripts run.
Compiler-discovered dependency closure is checked later by clean_build.run.
"""
from pathlib import Path
import hashlib,json,re,shutil
from clean_build import ID,relative,sha,inspect,filesystem_metadata,path_in

INCLUDE=re.compile(r'^(\s*#\s*include\s*)"([^"\n]+)"',re.M)
EXT={'.h','.hpp','.hxx','.hh','.inl','.inc','.c','.cc','.cpp','.cxx','.m','.mm','.S'}
MAX_BYTES=1024*1024*1024

def owned_audio_header_edits(recipe,read_source):
 """Unify recognized legacy copies only when native PCM is actually present.

 Inline bool-only readiness is layout-compatible but bypasses host stream setup.
 Unknown header variants fail rather than overwriting game-specific extensions.
 """
 canonical=(Path(__file__).parent/'runtime/native_audio/ps2_audio.h').read_text()
 legacy=canonical.replace('#include <span>\n','').replace('    void setAudioReady(bool ready);\n    void submitPcm(std::span<const int16_t> samples);\n    void resetPcm();',
     '    void setAudioReady(bool ready) { m_audioReady = ready; }')
 backends=[u for u in recipe['units'] if 'PS2AudioBackend::setAudioReady(bool ready)' in read_source(u['source']) and 'PS2AudioBackend::submitPcm(' in read_source(u['source'])]
 if not backends:return [],[]
 headers=sorted(f for f in recipe['files'] if Path(f).name=='ps2_audio.h')
 if not headers:raise ValueError('Native PCM implementation has no staged audio header')
 edits=[]
 for name in headers:
  old=read_source(name)
  if old not in (canonical,legacy):raise ValueError('Unrecognized native audio header variant: '+name)
  if old!=canonical:edits.append(dict(file=name,sha256=recipe['files'][name],before=old,after=canonical,reason='Reconcile native PCM caller declarations; remove legacy bool-only inline readiness'))
 contracts=[]
 for unit in backends:
  targets={t['id']:t for t in recipe['targets']}
  def consumes(t,seen=None):
   seen=set() if seen is None else seen
   if t in seen:return False
   seen.add(t);row=targets[t]
   return unit['id'] in row.get('units',[]) or any(consumes(n,seen) for n in row.get('uses',[]))
  for target in recipe['targets']:
   if target['kind']=='executable' and consumes(target['id']):
    contracts.append(dict(id='native-pcm-headers.'+unit['id']+'.'+target['id'],unit=unit['id'],target=target['id'],required_fragments=['PS2AudioBackend::setAudioReady(bool ready)'],header_basename='ps2_audio.h',header_reference=headers[0]))
 return edits,contracts

def reconcile_owned_audio_headers(root,recipe,records):
 """Apply owned declaration reconciliation to staged or merged source copies."""
 root=Path(root)
 edits,contracts=owned_audio_header_edits(recipe,lambda name:(root/name).read_text(errors='surrogateescape'))
 rows={r['staged']:r for r in records}
 for edit in edits:
  target=root/edit['file'];target.write_text(edit['after'])
  recipe['files'][edit['file']]=sha(target)
  rows[edit['file']].update(sha256=sha(target),owned_header_reconciliation=edit['reason'])
 known={c['id'] for c in recipe.get('source_contracts',[])}
 for contract in contracts:
  if contract['id'] not in known:recipe.setdefault('source_contracts',[]).append(contract)
 return [e['file'] for e in edits]

def relocate(recipe_path,ledger_path,destination):
 """Copy only a verified source inventory; preserve correction/link identities."""
 recipe_path=Path(recipe_path).resolve();ledger_path=Path(ledger_path).resolve();checked=inspect(recipe_path)
 root=checked['root'];recipe=checked['recipe'];ledger_pin=sha(ledger_path);ledger=json.loads(ledger_path.read_text())
 if ledger.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Relocation ledger covers a different source graph')
 rows={row['staged']:row for row in ledger.get('files',[])}
 if any(rows.get(name,{}).get('sha256')!=pin for name,pin in recipe['files'].items()):raise ValueError('Relocation ledger must cover every source input')
 if len({name.casefold() for name in recipe['files']})!=len(recipe['files']):raise ValueError('Source paths collide on a case-insensitive destination')
 out=Path(destination).resolve()
 if out.exists() or out.is_relative_to(root) or root.is_relative_to(out):raise ValueError('Use a fresh nonoverlapping source relocation directory')
 out.mkdir(parents=True);report=dict(schema=1,status='copying',source_recipe=str(recipe_path),recipe_sha256=checked['recipe_sha256'],staging_sha256=ledger_pin,source_files=len(recipe['files']),copied_files=0,historical_objects_copied=False,gameplay_verified=False)
 def save():(out/'relocation.json').write_text(json.dumps(report,indent=2)+'\n')
 save()
 try:
  for name,pin in recipe['files'].items():
   src=path_in(root,name);dst=path_in(out,name);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
   if sha(src)!=pin or sha(dst)!=pin:raise ValueError('Source changed during relocation: '+name)
   report['copied_files']+=1
   if report['copied_files']%512==0:save()
  for unit in recipe['units']:
   for name in [*unit.get('includes',[]),*(row['path'] for row in unit.get('include_paths',[]))]:path_in(out,name).mkdir(parents=True,exist_ok=True)
  shutil.copyfile(recipe_path,out/'recipe.json');shutil.copyfile(ledger_path,out/'staging.json')
  if sha(out/'recipe.json')!=checked['recipe_sha256'] or sha(out/'staging.json')!=ledger_pin:raise ValueError('Relocated recipe or ledger pin changed')
  if inspect(recipe_path)['recipe_sha256']!=checked['recipe_sha256'] or sha(ledger_path)!=ledger_pin:raise ValueError('Relocation inputs changed')
  inspect(out/'recipe.json');report.update(status='source_graph_relocated_not_built',recipe=str(out/'recipe.json'),staging=str(out/'staging.json'));save();return report
 except Exception as error:
  report.update(status='failed',error=str(error));save();raise

def stage(config_path,destination):
 config_path=Path(config_path).resolve();config_bytes=config_path.read_bytes();data=json.loads(config_bytes);config_digest=hashlib.sha256(config_bytes).hexdigest()
 if data.get('schema')!=1:raise ValueError('Source graph config requires schema 1')
 out=Path(destination).resolve()
 if out.exists():raise ValueError('Preserve prior source graphs; use a new destination')
 roots={}
 for name,value in data.get('roots',{}).items():
  if not ID.fullmatch(name):raise ValueError('Invalid source root name')
  p=Path(value)
  if not p.is_absolute() or p.is_symlink() or not p.is_dir():raise ValueError('Source root must be an absolute real directory')
  roots[name]=p.resolve()
 if not roots:raise ValueError('Declare source roots')
 if any(out.is_relative_to(p) or p.is_relative_to(out) for p in roots.values()):raise ValueError('Source graph destination overlaps input roots')
 # Nested roots use the most specific owner, preserving explicit header overlays.
 ordered=sorted(roots.items(),key=lambda x:len(x[1].parts),reverse=True)
 def mapped(path):
  path=Path(path)
  if path.is_symlink():raise ValueError('Source symlinks are not staged')
  path=path.resolve()
  for name,root in ordered:
   if path.is_relative_to(root):return Path('sources')/name/path.relative_to(root)
  raise ValueError('Source/include is outside declared roots: '+str(path))
 originals={};total=0
 for name,root in roots.items():
  for p in sorted(root.rglob('*')):
   if p.is_symlink():
    if p.suffix in EXT:raise ValueError('Source roots contain a source symlink: '+str(p))
    continue # Unrelated asset/build-directory links are never copied or followed.
   if not p.is_file() or p.suffix not in EXT or filesystem_metadata(p):continue
   # Keep each declared root's include topology, including nested-root aliases.
   # A parent -I directory must still resolve e.g. Stubs/Unimplemented.h.
   dest=Path("sources")/name/p.relative_to(root)
   if dest in originals:continue
   total+=p.stat().st_size
   if total>MAX_BYTES:raise ValueError('Bounded source inventory exceeds 1 GiB')
   originals[dest]=p
 units=[]
 for raw in data.get('units',[]):
  unit=dict(raw);src=Path(unit.pop('source'))
  dest=mapped(src)
  if dest not in originals:raise ValueError('Unit is not a staged source file')
  unit['source']=str(dest)
  unit['includes']=[str(Path('sources')/name) for name in unit.pop('include_roots',[]) if name in roots]
  if len(unit['includes'])!=len(raw.get('include_roots',[])):raise ValueError('Unknown include root')
  unit['include_paths']=[dict(kind=x['kind'],path=str(mapped(Path(x['path'])))) for x in unit.get('include_paths',[])]
  unit['force_include']=[str(mapped(p)) for p in unit.get('force_include',[])]
  units.append(unit)
 recipe=dict(schema=1,source_only=True,files={},units=units,targets=data.get('targets',[]))
 contexts={}
 all_contexts=[]
 for unit in data.get('units',[]):
  dirs=[Path(x['path']).resolve() for x in unit.get('include_paths',[]) if x['kind']=='-iquote']
  dirs += [roots[n] for n in unit.get('include_roots',[])]
  dirs += [Path(x['path']).resolve() for x in unit.get('include_paths',[]) if x['kind'] in {'-I','-isystem'}]
  contexts.setdefault(Path(unit['source']).resolve(),[]).append(dirs)
  all_contexts.append(dirs)
 def quoted_parent(origin,inc):
  direct=(origin.parent/inc).resolve()
  if direct.is_file():return direct
  matches=set()
  for dirs in contexts.get(origin,all_contexts):
   for folder in dirs:
    candidate=(folder/inc).resolve()
    if candidate.is_file():matches.add(candidate);break
  if not matches:return None
  if len(matches)!=1:raise ValueError('Parent include resolution is ambiguous: '+str(origin)+' -> '+str(inc))
  return matches.pop()
 records=[];out.mkdir(parents=True)
 try:
  for name in roots:(out/'sources'/name).mkdir(parents=True,exist_ok=True)
  for unit in units:
   for directory in [*unit['includes'],*(x['path'] for x in unit.get('include_paths',[]))]:(out/directory).mkdir(parents=True,exist_ok=True)
  for dest,p in originals.items():
   source_bytes=p.read_bytes()
   try:content=source_bytes.decode('utf-8');encoding='utf-8'
   except UnicodeDecodeError:content=source_bytes.decode('latin-1');encoding='latin-1'
   before=hashlib.sha256(source_bytes).hexdigest();rewrites=[]
   def replace(m):
    inc=Path(m.group(2))
    if not inc.is_absolute():
     # Parent-relative quoted includes can otherwise escape the declared snapshot.
     if '..' not in inc.parts:return m.group(0)
     inc=quoted_parent(p,inc)
     if inc is None:
      rewrites.append(dict(original=m.group(2),deferred=True,reason='No source-tree resolution; compiler dependency closure required'))
      return m.group(0)
    target=mapped(inc)
    if target not in originals:raise ValueError('Absolute/parent include is not staged: '+str(inc))
    import os
    value=os.path.relpath(out/target,(out/dest).parent)
    rewrites.append(dict(original=m.group(2),staged=value))
    return m.group(1)+'"'+value+'"'
   content=INCLUDE.sub(replace,content)
   target=out/dest;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content.encode(encoding))
   if sha(p)!=before:raise ValueError('Source changed during staging: '+str(p))
   recipe['files'][str(dest)]=sha(target)
   records.append(dict(original=str(p),original_sha256=before,staged=str(dest),sha256=sha(target),includes_rewritten=rewrites,encoding=encoding))
  reconcile_owned_audio_headers(out,recipe,records)
  path=out/'recipe.json';path.write_text(json.dumps(recipe,indent=2)+'\n')
  checked=inspect(path)
  if sha(config_path)!=config_digest:raise ValueError('Source graph config changed during staging')
  report=dict(schema=1,status='source_graph_staged_not_built',config_sha256=config_digest,
   recipe_sha256=checked['recipe_sha256'],source_files=len(records),source_bytes=total,
   units=len(units),historical_objects_copied=False,dependency_closure_verified=False,
   equivalence_verified=False,gameplay_verified=False,files=records)
  (out/'staging.json').write_text(json.dumps(report,indent=2)+'\n')
  return report
 except Exception as error:
  # Retain the partial snapshot and exact failure; never erase evidence or inputs.
  (out/'failed.json').write_text(json.dumps(dict(status='failed',error=str(error)),indent=2))
  raise


def revise(recipe_path,ledger_path,updates,recipe_output,ledger_output):
 """Revise compiler settings without losing the source staging provenance.

 Sources and target topology cannot be swapped by this operation. A new recipe
 invalidates old build/conformance evidence; output files are never overwritten.
 """
 recipe_path=Path(recipe_path).resolve();ledger_path=Path(ledger_path).resolve()
 recipe_output=Path(recipe_output).absolute();ledger_output=Path(ledger_output).absolute()
 checked=inspect(recipe_path);root=checked['root']
 if recipe_output.parent.resolve()!=root or ledger_output.parent.resolve()!=root or recipe_output==ledger_output:raise ValueError('Recipe revisions must stay beside the pinned staged sources')
 if any(p.exists() or p.is_symlink() for p in [recipe_output,ledger_output]):raise ValueError('Preserve prior recipe/ledger revisions')
 ledger_bytes=ledger_path.read_bytes();ledger=json.loads(ledger_bytes)
 if ledger.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Prior staging ledger does not cover this recipe')
 if not isinstance(updates,dict) or not updates:raise ValueError('Declare explicit compiler setting updates')
 units={u['id']:u for u in checked['recipe']['units']}
 allowed={'standard','flags','defines','includes','include_paths','force_include','sdk_framework_search'}
 for name,patch in updates.items():
  if name not in units or not isinstance(patch,dict) or not patch or set(patch)-allowed:raise ValueError('Recipe revisions may update only existing unit compiler settings')
  units[name].update(patch)
 # Inspect in the same root so relative source/include paths retain their meaning.
 import tempfile
 with tempfile.NamedTemporaryFile(mode='w',dir=root,suffix='.json') as temp:
  json.dump(checked['recipe'],temp);temp.flush();inspect(temp.name)
 if sha(recipe_path)!=checked['recipe_sha256'] or sha(ledger_path)!=hashlib.sha256(ledger_bytes).hexdigest():raise ValueError('Recipe/ledger changed during revision')
 with recipe_output.open('x') as f:json.dump(checked['recipe'],f,indent=2);f.write('\n')
 ledger.setdefault('recipe_revisions',[]).append(dict(previous_recipe_sha256=checked['recipe_sha256'],previous_ledger_sha256=hashlib.sha256(ledger_bytes).hexdigest(),unit_settings=updates))
 ledger.update(recipe_sha256=sha(recipe_output),status='source_graph_revised_not_built',dependency_closure_verified=False,equivalence_verified=False,gameplay_verified=False)
 with ledger_output.open('x') as f:json.dump(ledger,f,indent=2);f.write('\n')
 return dict(recipe=str(recipe_output),recipe_sha256=sha(recipe_output),staging=str(ledger_output),staging_sha256=sha(ledger_output),status=ledger['status'],changed_units=list(updates),equivalence_verified=False)
