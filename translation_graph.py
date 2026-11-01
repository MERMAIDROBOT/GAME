"""Replay qualified EE instruction rules into a retained native source graph.

Fresh translation bytes remain authoritative. Unknown source differences stop
before copying; title amendments, source topology and original inputs survive.
"""
from pathlib import Path
import hashlib,json,re
from clean_build import inspect,path_in,sha
from graph_amend import amend
import knowledge


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ee_source(text):
 from sdk_policy import LITERALS
 from branches import RECORD
 code=LITERALS.sub('',text)
 return bool(re.search(r'\bR5900Context\s*\*\s*ctx\b',code) and RECORD.search(text))


TABLE_ROW=re.compile(r'^[ \t]*g_ps2RecompiledFunctionTable\[(\d+)\][ \t]*=[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*;[ \t]*//[ \t]*0x([0-9a-fA-F]+)[ \t]*\n?',re.M)


def function_scope(text,symbol):
 from sdk_policy import LITERALS
 code=LITERALS.sub(lambda m:''.join('\n' if c=='\n' else ' ' for c in m[0]),text)
 definitions=list(re.finditer(r'\bvoid\s+'+re.escape(symbol)+r'\s*\([^;{}]*\bR5900Context\s*\*\s*ctx\b[^;{}]*\)\s*\{',code))
 if len(definitions)!=1:return None
 start=definitions[0].end()-1;depth=0
 for end in range(start,len(code)):
  if code[end]=='{':depth+=1
  elif code[end]=='}':
   depth-=1
   if depth==0:return text[start:end+1],code[start:end+1]
 return None


def additive_resume_table(old,fresh,sources,pins):
 """Admit only additive registrations for already-known translated bodies."""
 if 'GeneratedFunctionTableInitializer' not in old:return None
 if TABLE_ROW.sub('',old)!=TABLE_ROW.sub('',fresh):return None
 base=re.search(r'g_ps2RecompiledFunctionTableBase\s*=\s*0x([0-9a-fA-F]+)u;',fresh)
 end=re.search(r'g_ps2RecompiledFunctionTableEnd\s*=\s*0x([0-9a-fA-F]+)u;',fresh)
 if not base or not end:return None
 base,end=int(base[1],16),int(end[1],16)
 def parse(text):
  rows={}
  for match in TABLE_ROW.finditer(text):
   index=int(match[1]);address=int(match[3],16)
   if index in rows or address!=base+index*4 or address<base or address>=end:raise ValueError('Unqualified function-table slot/address')
   rows[index]=(match[2],address)
  return rows
 before,after=parse(old),parse(fresh)
 if not before or any(after.get(index)!=row for index,row in before.items()):return None
 additions=set(after)-set(before);known={symbol for symbol,address in before.values()}
 for index in additions:
  symbol,address=after[index];name=symbol+'.cpp';source=sources.get(name)
  if symbol not in known or source is None:return None
  raw=source.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=pins[name]:raise ValueError('Resume target source changed')
  text=raw.decode();suffix=re.search(r'_0x([0-9a-fA-F]+)$',symbol)
  if not suffix:return None
  entry=int(suffix[1],16)
  if (entry-base)%4 or before.get((entry-base)//4)!=(symbol,entry):return None
  scope=function_scope(text,symbol)
  if scope is None:return None
  raw_scope,code_scope=scope
  label=re.search(r'(?mi)^[ \t]*label_0*'+format(address,'x')+r':',code_scope)
  instruction=re.search(r'// 0x0*'+format(address,'x')+r': 0x[0-9a-fA-F]+',raw_scope,re.I)
  if not label or not instruction:return None
 return fresh,len(additions)


def plan(recipe_path,staging_path,generated,identity,pins,substitutions=None):
 from pipeline import generated_sources
 if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError('Generated graph replay needs an executable pin')
 checked=inspect(recipe_path);recipe=checked['recipe'];root=checked['root']
 ledger=json.loads(Path(staging_path).read_text())
 if ledger.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Translation replay ledger does not cover recipe')
 rows={r['staged']:r for r in ledger.get('files',[])}
 if any(rows.get(n,{}).get('sha256')!=pin for n,pin in recipe['files'].items()):raise ValueError('Translation replay ledger must cover all source files')
 for entry in ledger.get('source_amendments',[]):
  revision=entry.get('title_revision')
  if revision and revision.get('sha256')!=identity:raise ValueError('Retained graph belongs to another executable')
 accepted={p.name:p for p in generated_sources(generated)}
 if not isinstance(pins,dict) or set(pins)!=set(accepted) or any(sha(p)!=pins[n] for n,p in accepted.items()):raise ValueError('Accepted generated source manifest changed')
 substitutions=substitutions or [];declared={}
 for entry in substitutions:
  if not isinstance(entry,dict):raise ValueError('Generated substitution must be an object')
  name=entry.get('source')
  if name not in accepted or name in declared or entry.get('executable_sha256')!=identity or entry.get('generated_sha256')!=pins[name] or recipe['files'].get(entry.get('staged'))!=entry.get('staged_sha256') or not isinstance(entry.get('reason'),str) or not entry['reason'].strip():raise ValueError('Generated substitution pins changed before replay')
  declared[name]=entry
 bank=knowledge.load();edits=[];bindings={};blockers=[];alternates=[];bound_names=set();counts=dict(files_checked=0,files_changed=0,instructions=0,corrected=0,resume_registrations=0)
 fresh_table=accepted['register_functions.cpp'].read_text() if 'register_functions.cpp' in accepted else None
 for name,pin in recipe['files'].items():
  if Path(name).suffix.lower() not in {'.cpp','.cxx','.cc','.c++'}:continue
  raw=path_in(root,name).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('Graph source changed during replay qualification')
  try:encoding='utf8';text=raw.decode(encoding)
  except UnicodeDecodeError:encoding='latin1';text=raw.decode(encoding)
  updated=text
  # The IOP is 32-bit. Never apply the EE's signed-width rules merely because
  # another processor's source has instruction-address comments.
  if ee_source(text):
   try:
    result=knowledge.rewrite_source(text,identity=identity,bank=bank,label=name)
    counts['files_checked']+=1;counts['instructions']+=result['instructions'];counts['corrected']+=result['corrected'];updated=result['text']
   except ValueError as error:
    blockers.append(dict(staged=name,reason='unqualified_retained_emission',detail=str(error)))
  original_name=Path(rows[name].get('original','')).name
  reason='Opcode-qualified EE instruction replay against sealed fresh translation'
  if original_name=='register_functions.cpp' and fresh_table is not None and updated!=fresh_table:
   upgrade=additive_resume_table(updated,fresh_table,accepted,pins)
   if upgrade is not None:
    updated,count=upgrade;counts['resume_registrations']+=count;reason='Additive resume entries for existing translated functions with verified slot/address, instruction and label'
  output=updated.encode(encoding);output_pin=hashlib.sha256(output).hexdigest()
  if original_name in accepted and original_name not in declared:
   if output_pin!=pins[original_name]:alternates.append(dict(source=original_name,staged=name,reason='retained_title_alternate_not_fresh_translation',staged_sha256=pin,qualified_sha256=output_pin,accepted_sha256=pins[original_name]))
   else:
    bindings[name]=dict(original=str(accepted[original_name].resolve()),original_sha256=pins[original_name]);bound_names.add(original_name)
  if output!=raw:
   edits.append(dict(file=name,sha256=pin,before=text,after=updated,reason=reason))
 from runtime_header_replay import plan as header_plan
 headers=header_plan(root,recipe['files']);edits+=headers['edits'];blockers+=headers['blockers']
 counts['files_changed']=len(edits);counts['scalar_headers']=len(headers['headers'])
 for name in sorted(set(accepted)-bound_names-set(declared)):
  blockers.append(dict(source=name,reason='no_qualified_staged_copy_of_fresh_translation'))
 return dict(status='blocked' if blockers else 'qualified_not_built',recipe_sha256=checked['recipe_sha256'],staging_sha256=sha(staging_path),edits=edits,bindings=bindings,retained_alternates=alternates,blockers=blockers,counts=counts)


def prepare_native(profile,private):
 from pipeline import boot_executable,require_accepted_translation
 private=Path(private).resolve();spec=profile['native_source_graph']
 stage=require_accepted_translation(private);pins=stage.get('generated_sources')
 implementation=knowledge.translation_implementation()
 if stage.get('translation_rule_implementation')!=implementation:raise ValueError('Fresh translation predates current instruction rules; retranslate')
 executable=boot_executable(private/'disc-root',profile);identity=sha(executable)
 if identity!=profile.get('expected_sha256'):raise ValueError('Graph replay requires the actual executable revision')
 recipe=path_in(private,spec['recipe']);ledger=path_in(private,spec['staging'])
 if sha(recipe)!=spec['sha256'] or sha(ledger)!=spec['staging_sha256']:raise ValueError('Graph replay input pins changed')
 if not isinstance(pins,dict) or not pins:raise ValueError('Translation has no sealed source manifest; retranslate')
 from pipeline import generated_sources
 actual={p.name:sha(p) for p in generated_sources(private/'generated')}
 if actual!=pins:raise ValueError('Accepted generated source manifest changed')
 # Cached output must never hide a changed/missing retained input.
 inspect(recipe)
 contract=dict(recipe=spec['recipe'],staging=spec['staging'],executable=str(executable.relative_to(private)),recipe_sha256=spec['sha256'],staging_sha256=spec['staging_sha256'],executable_sha256=identity,
  generated_sources=pins,translation_implementation=implementation,implementation={str(p.relative_to(Path(__file__).parent)):sha(p) for p in [Path(__file__),Path(__file__).with_name('graph_amend.py'),Path(__file__).with_name('runtime_header_replay.py'),Path(__file__).parent/'tools/ps2_scalar_fpu.h']},substitutions=spec.get('generated_substitutions',[]))
 base=private/'analysis/translation-rebase'/fingerprint(contract)
 base=path_in(private,str(base.relative_to(private)));base.mkdir(parents=True,exist_ok=True);receipt_path=base/'receipt.json'
 if receipt_path.exists():
  receipt=verify_receipt(dict(path=str(receipt_path.relative_to(private)),sha256=sha(receipt_path)),private)
  if receipt.get('contract')!=contract:raise ValueError('Translation replay receipt changed')
 else:
  planned=plan(recipe,ledger,private/'generated',identity,pins,spec.get('generated_substitutions'))
  (base/'audit.json').write_text(json.dumps({k:v for k,v in planned.items() if k not in {'edits','bindings'}},indent=2)+'\n')
  if planned['blockers']:
   first=planned['blockers'][0];raise ValueError('Retained graph needs qualified title integration: '+first.get('source',first.get('staged',first['reason'])))
  graph=dict(recipe=spec['recipe'],sha256=spec['sha256'],staging=spec['staging'],staging_sha256=spec['staging_sha256'])
  if planned['edits']:
   for i in range(100):
    attempt=path_in(private,str((base/('attempt-'+str(i).zfill(3))).relative_to(private)))
    if not attempt.exists():break
   else:raise ValueError('Too many failed translation replay attempts; review local receipts')
   attempt.mkdir()
   config=dict(schema=1,recipe=str(recipe),recipe_sha256=spec['sha256'],staging=str(ledger),staging_sha256=spec['staging_sha256'],title_revision=dict(executable=str(executable),sha256=identity),edits=planned['edits'])
   config_path=attempt/'replay.json';config_path.write_text(json.dumps(config,ensure_ascii=True)+'\n')
   result=amend(config_path,attempt/'graph');out_ledger=Path(result['staging']);data=json.loads(out_ledger.read_text())
   for row in data['files']:
    if row['staged'] in planned['bindings']:
     row.setdefault('translation_rebases',[]).append(dict(previous_original=row['original'],previous_original_sha256=row.get('original_sha256'),executable_sha256=identity))
     row.update(planned['bindings'][row['staged']])
   data['translation_rule_rebase']=dict(contract_sha256=fingerprint(contract),executable_sha256=identity,counts=planned['counts'])
   out_ledger.write_text(json.dumps(data,indent=2)+'\n')
   graph=dict(recipe=str(Path(result['recipe']).relative_to(private)),sha256=sha(result['recipe']),staging=str(out_ledger.relative_to(private)),staging_sha256=sha(out_ledger))
  if {p.name:sha(p) for p in generated_sources(private/'generated')}!=pins:raise ValueError('Fresh translation changed during source replay')
  receipt=dict(schema=1,status='translation_rules_replayed_not_built',contract=contract,graph=graph,counts=planned['counts'],gameplay_verified=False)
  receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
 graph=receipt['graph'];out_recipe=path_in(private,graph['recipe']);out_ledger=path_in(private,graph['staging'])
 if sha(out_recipe)!=graph['sha256'] or sha(out_ledger)!=graph['staging_sha256']:raise ValueError('Replayed graph receipt pins changed')
 inspected=inspect(out_recipe)
 updated=dict(spec,**graph)
 if spec.get('generated_substitutions'):
  updated['generated_substitutions']=[dict(row,staged_sha256=inspected['recipe']['files'][row['staged']]) for row in spec['generated_substitutions']]
 return dict(profile,native_source_graph=updated),dict(path=str(receipt_path.relative_to(private)),sha256=sha(receipt_path))


def receipt_implementation_matches(record,expected):
 """Only exact reviewed transitions qualify; source/rule pins still apply."""
 if record==expected:return True
 table=json.loads(Path(__file__).with_name('replay_receipt_versions.json').read_text())
 if table.get('schema')!=1 or not isinstance(table.get('transitions'),list) or len(table['transitions'])>16:raise ValueError('Invalid replay receipt version table')
 return any(item.get('from')==record and item.get('to')==expected for item in table['transitions'])


def verify_receipt(entry,private):
 private=Path(private).resolve();path=path_in(private,entry['path'])
 if sha(path)!=entry.get('sha256'):raise ValueError('Translation replay receipt changed')
 receipt=json.loads(path.read_text())
 if receipt.get('schema')!=1 or receipt.get('status')!='translation_rules_replayed_not_built':raise ValueError('Translation replay has no qualified source receipt')
 contract=receipt['contract'];graph=receipt['graph']
 expected={str(p.relative_to(Path(__file__).parent)):sha(p) for p in [Path(__file__),Path(__file__).with_name('graph_amend.py'),Path(__file__).with_name('runtime_header_replay.py'),Path(__file__).parent/'tools/ps2_scalar_fpu.h']}
 if contract['translation_implementation']!=knowledge.translation_implementation() or not receipt_implementation_matches(contract.get('implementation'),expected):raise ValueError('Translation replay implementation changed')
 for key,pin in [('recipe','recipe_sha256'),('staging','staging_sha256'),('executable','executable_sha256')]:
  if sha(path_in(private,contract[key]))!=contract[pin]:raise ValueError('Translation replay input changed')
 from pipeline import generated_sources
 if {p.name:sha(p) for p in generated_sources(private/'generated')}!=contract['generated_sources']:raise ValueError('Translation replay generated sources changed')
 if sha(path_in(private,graph['recipe']))!=graph['sha256'] or sha(path_in(private,graph['staging']))!=graph['staging_sha256']:raise ValueError('Translation replay output pins changed')
 inspect(path_in(private,graph['recipe']))
 return receipt
