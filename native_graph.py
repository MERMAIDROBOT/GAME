"""Connect an explicitly pinned source graph to native build/package receipts."""
from pathlib import Path
import re,json,os
from clean_build import inspect,run,path_in,sha



def conformance_cases(cases):
 """Declarative synthetic runtime modes; no shell or arbitrary environment."""
 if not isinstance(cases,list) or len(cases)>16:raise ValueError('Invalid source conformance cases')
 allowed={'PS2_PORT_HEADLESS':{'1'},'PS2_PORT_GS_THREAD':{'0','1'},'PS2_PORT_HOST_VSYNC_TEST':{'0','1'}}
 seen=set();result=[]
 for case in cases:
  if not isinstance(case,dict) or set(case)!={'name','environment'}:raise ValueError('Invalid source conformance case')
  name=case['name'];env=case['environment']
  if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',name) or name in seen:raise ValueError('Invalid or duplicate conformance case name')
  if not isinstance(env,dict) or any(key not in allowed or not isinstance(value,str) or value not in allowed[key] for key,value in env.items()):raise ValueError('Invalid conformance environment')
  seen.add(name);result.append(dict(name=name,environment=dict(env)))
 return result


def component_frameworks(spec,private):
 """Combine separately source-built native graphics with the runner graph."""
 private=Path(private).resolve();result={}
 entries=spec.get('native_components',[])
 if not isinstance(entries,list):raise ValueError('Native components must be a list')
 for entry in entries:
  if not isinstance(entry,dict) or set(entry)!={'kind','receipt','sha256','framework'} or entry['kind']!='native_graphics':raise ValueError('Unknown native source component declaration')
  name=entry['framework']
  if not isinstance(name,str) or not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',name) or name in result:raise ValueError('Invalid or duplicate component framework name')
  receipt=path_in(private,entry['receipt'])
  if sha(receipt)!=entry['sha256']:raise ValueError('Native component receipt differs from profile pin')
  import native_components,pipeline as lab
  artifact=native_components.verify(lab,receipt)
  if not Path(artifact['path']).resolve().is_relative_to(private):raise ValueError('Native component must come from private source build storage')
  result[name]=artifact
 return result


def specification(profile,private):
 spec=profile.get('native_source_graph')
 if spec is None:return None
 if not isinstance(spec,dict):raise ValueError('native_source_graph must be an object')
 private=Path(private).resolve();recipe=path_in(private,spec.get('recipe',''))
 if sha(recipe)!=spec.get('sha256'):raise ValueError('Native source graph differs from profile pin')
 graph=inspect(recipe);targets={t['id']:t for t in graph['recipe']['targets']}
 required_contracts=spec.get('required_source_contracts',[])
 if not isinstance(required_contracts,list) or any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',n) for n in required_contracts) or len(set(required_contracts))!=len(required_contracts):raise ValueError('Invalid required source contracts')
 if not set(required_contracts)<=set(graph['source_contracts']):raise ValueError('Native source graph dropped required source contracts')
 runner=spec.get('runner')
 if runner not in targets or targets[runner]['kind']!='executable':raise ValueError('Native graph needs an executable runner target')
 reachable_targets=set();runner_units=set()
 def visit(name):
  if name in reachable_targets:return
  reachable_targets.add(name);target=targets[name];runner_units.update(target.get('units',[]))
  for child in target.get('uses',[]):visit(child)
 visit(runner)
 tests=spec.get('tests')
 if not isinstance(tests,list) or not tests or runner in tests or len(set(tests))!=len(tests):raise ValueError('Declare distinct source-built conformance test targets')
 if any(n not in targets or targets[n]['kind']!='executable' for n in tests):raise ValueError('Conformance targets must be source-built executables')
 cases=conformance_cases(spec.get('test_cases',[]))
 components=component_frameworks(spec,private)
 from platform_sdks import frameworks as sdk_frameworks
 sdks=sdk_frameworks(spec,private)
 if set(components)&set(sdks):raise ValueError('Platform SDK has competing source providers')
 components.update(sdks)
 frameworks=spec.get('frameworks',{})
 if not isinstance(frameworks,dict):raise ValueError('Framework target map must be an object')
 if set(frameworks)&set(components):raise ValueError('Native framework has competing source providers')
 for name,target in frameworks.items():
  if not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',name):raise ValueError('Invalid packaged framework name')
  if target not in targets or targets[target]['kind']!='shared':raise ValueError('Framework needs a source-built shared target')
 ledger_path=path_in(private,spec.get('staging',''))
 if sha(ledger_path)!=spec.get('staging_sha256'):raise ValueError('Source staging ledger differs from profile pin')
 ledger=json.loads(ledger_path.read_text())
 if ledger.get('recipe_sha256')!=graph['recipe_sha256']:raise ValueError('Staging ledger does not cover this build recipe')
 for amendment in ledger.get('source_amendments',[]):
  if 'title_revision' not in amendment:continue
  revision=amendment['title_revision']
  if not isinstance(revision,dict) or not isinstance(revision.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',revision['sha256']) or revision['sha256']!=profile.get('expected_sha256'):raise ValueError('Source amendment belongs to a different executable revision')
 if profile.get('native_launch',{}).get('environment',{}).get('PS2_PORT_HW_GS_ZERO_COPY')=='1':
  transport=ledger.get('surface_transport',{})
  if not isinstance(transport,dict) or not transport.get('window') or not transport.get('frontend') or not isinstance(transport.get('headers'),dict):raise ValueError('Surface transport must be staged into the actual source runner')
  linked_sources={unit['source'] for unit in graph['recipe']['units'] if unit['id'] in runner_units}
  if not {transport['window'],transport['frontend']}<=linked_sources:raise ValueError('Surface transport must belong to linked runner sources')
  headers=transport['headers']
  if not headers or any(graph['recipe']['files'].get(name)!=digest for name,digest in headers.items()):raise ValueError('Surface transport header pins differ from runner graph')
  if not {'mac_surface_presenter.hpp','surface_consumer.hpp','surface_token.hpp'}<={Path(name).name for name in headers}:raise ValueError('Surface transport header inventory is incomplete')

 from pipeline import generated_sources
 generated=path_in(private,'generated')
 accepted=list(generated_sources(generated));accepted_by_name={p.name:p for p in accepted}
 if any(p.is_symlink() for p in accepted):raise ValueError('Accepted generated sources may not be symlinks')
 substitutions={}
 declared=spec.get('generated_substitutions',[])
 if not isinstance(declared,list):raise ValueError('Generated substitutions must be a list')
 for entry in declared:
  if not isinstance(entry,dict):raise ValueError('Generated substitution must be an object')
  name=entry.get('source');identity=profile.get('expected_sha256','')
  if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity) or entry.get('executable_sha256')!=identity:raise ValueError('Generated substitution requires the pinned executable revision')
  if name not in accepted_by_name or name in substitutions:raise ValueError('Generated substitution must name a unique accepted source')
  if not isinstance(entry.get('reason'),str) or not entry['reason'].strip():raise ValueError('Generated substitution needs a reason')
  staged=entry.get('staged')
  if sha(accepted_by_name[name])!=entry.get('generated_sha256') or graph['recipe']['files'].get(staged)!=entry.get('staged_sha256') or not entry.get('staged_sha256'):raise ValueError('Generated substitution source pins differ')
  substitutions[name]=entry
 originals={};identities={};content_identities={}
 for f in ledger.get('files',[]):
  originals.setdefault(os.path.abspath(f['original']),[]).append(f)
  identities.setdefault((Path(f['original']).name,f.get('original_sha256')),[]).append(f)
  # An unchanged body can legitimately be compiled from a renamed source.
  content_identities.setdefault(f['sha256'],[]).append(f)
 required=[];alternatives=[];origin_resolution=[]
 for source in accepted:
  if source.name in substitutions:
   entry=substitutions[source.name];choices=[entry['staged']]
   required.append(choices[0]);alternatives.append(choices)
   origin_resolution.append(dict(source=str(source),sha256=sha(source),resolution='revision_gated_substitution',staged_alternatives=choices,reason=entry['reason'],executable_sha256=entry['executable_sha256']))
   continue
  candidates=originals.get(str(source),[])
  resolution='exact_path'
  if not candidates:
   # Fresh conversions and relocated private runs have new absolute paths.
   # Require both source identity and bytes; filenames alone never suffice.
   candidates=identities.get((source.name,sha(source)),[]);resolution='pinned_content_identity'
  digest=sha(source)
  if any(r.get('original_sha256')!=digest for r in candidates):raise ValueError('Native graph changes accepted generated source origin: '+source.name)
  candidates=list(candidates);known_paths={r['staged'] for r in candidates}
  derived=[r for r in identities.get((source.name,digest),[]) if r['staged'] not in known_paths]
  if derived:
   resolution=(resolution+'_with_pinned_source_identities') if candidates else 'pinned_content_identity'
   candidates+=derived;known_paths.update(r['staged'] for r in derived)
  aliases=[r for r in content_identities.get(digest,[]) if r['staged'] not in known_paths]
  if aliases:
   resolution=(resolution+'_with_byte_identical_relocated_sources') if candidates else 'byte_identical_relocated_source'
   candidates+=aliases
  if not candidates:raise ValueError('Native graph omits or changes accepted generated source: '+source.name)
  if any(graph['recipe']['files'].get(r['staged'])!=r['sha256'] for r in candidates):raise ValueError('Staged generated source hash differs from graph')
  choices=list(dict.fromkeys(r['staged'] for r in candidates))
  required.append(choices[0]);alternatives.append(choices)
  origin_resolution.append(dict(source=str(source),sha256=sha(source),resolution=resolution,staged_alternatives=choices))
 if not required:raise ValueError('Native graph needs accepted generated source inputs')
 abi=None
 if spec.get('abi_layouts') is not None:
  entry=spec['abi_layouts']
  if not isinstance(entry,dict):raise ValueError('ABI layout declaration must be an object')
  abi=path_in(private,entry.get('config',''))
  if sha(abi)!=entry.get('sha256'):raise ValueError('ABI configuration differs from profile pin')
  config=json.loads(abi.read_text())
  if not (config.get('records') or config.get('functions')):raise ValueError('ABI configuration needs shared records or functions')
  for record in config.get('records',[])+config.get('functions',[]):
   for probe in record.get('probes',[]):
    if Path(probe.get('recipe','')).resolve()!=recipe or probe.get('recipe_sha256')!=graph['recipe_sha256']:raise ValueError('ABI probes must check the actual native graph')
 return dict(recipe=recipe,spec=spec,private=private,test_cases=cases,component_frameworks=components,required_generated=required,generated_alternatives=alternatives,generated_origin_resolution=origin_resolution,abi_config=abi,runner_units=sorted(runner_units),runner_targets=sorted(reachable_targets))



def generated_coverage(checked,report):
 used={p for unit,deps in report['dependencies'].items() if unit in checked['runner_units'] for p in deps}
 missing=[]
 for index,choices in enumerate(checked['generated_alternatives']):
  if not set(choices)&used:
   origin=checked.get('generated_origin_resolution',[])
   missing.append(dict(source=origin[index]['source'] if index<len(origin) else checked['required_generated'][index],alternatives=choices))
 return dict(status='missing_compiled_sources' if missing else 'compiled_source_coverage_complete',expected=len(checked['generated_alternatives']),matched=len(checked['generated_alternatives'])-len(missing),missing=missing,runner_units=len(checked['runner_units']),gameplay_verified=False)


def build(profile,private):
 declared=profile.get('native_source_graph')
 if declared is None:return None
 if not isinstance(declared,dict):raise ValueError('native_source_graph must be an object')
 if type(declared.get('replay_translation_rules',False)) is not bool:raise ValueError('Translation replay must be an explicit boolean')
 if declared.get('native_components'):
  entries=declared['native_components']
  if not isinstance(entries,list) or len(entries)>16:raise ValueError('Invalid native component declarations')
  names=[entry.get('framework') if isinstance(entry,dict) else None for entry in entries]
  if any(not isinstance(n,str) or not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',n) for n in names) or len(set(names))!=len(names):raise ValueError('Invalid or duplicate component framework name')
  prepared=[]
  for entry in entries:
   if 'build' not in entry:prepared.append(entry);continue
   if set(entry)!={'kind','build','framework'} or entry['kind']!='native_graphics':raise ValueError('Unknown native component source build declaration')
   import native_components,pipeline as lab
   artifact=native_components.provision(lab,private,entry['build'])
   receipt=Path(artifact['component_receipt']['path']).resolve()
   if not receipt.is_relative_to(Path(private).resolve()):raise ValueError('Native provision receipt escaped private build storage')
   prepared.append(dict(kind='native_graphics',framework=entry['framework'],receipt=str(receipt.relative_to(Path(private).resolve())),sha256=artifact['component_receipt']['sha256']))
  declared=dict(declared,native_components=prepared);profile=dict(profile,native_source_graph=declared)
  component_frameworks(declared,private) # Fail before expensive source staging.
 verify_source_chain(dict(source_graph_recipe_sha256=declared.get('sha256'),source_lineage=declared.get('source_lineage',[])),private)
 correction_receipt=None
 if declared.get('corrections') is not None:
  from source_corrections import prepare_native
  profile,correction_receipt=prepare_native(profile,private)
 translation_receipt=None
 if declared.get('replay_translation_rules',False):
  from translation_graph import prepare_native as prepare_translation
  profile,translation_receipt=prepare_translation(profile,private)
 checked=specification(profile,private)
 spec=checked['spec'];out=path_in(checked['private'],spec.get('build_directory','native-source-build'))
 cache=spec.get('source_cache');cache_options={}
 if cache is not None:
  if not isinstance(cache,dict) or set(cache)!={'build','receipt_sha256','recipe','recipe_sha256'}:raise ValueError('Declare pinned source cache receipt and recipe')
  directory=path_in(checked['private'],cache['build']);previous=path_in(checked['private'],cache['recipe'])
  if sha(directory/'receipt.json')!=cache['receipt_sha256'] or sha(previous)!=cache['recipe_sha256']:raise ValueError('Source cache declaration pins changed')
  from clean_build import direct_source_cache
  selected_cache=direct_source_cache(directory,previous,checked['private'],receipt_sha256=cache['receipt_sha256'],recipe_sha256=cache['recipe_sha256'])
  cache_options=dict(reuse_build=selected_cache['build'],reuse_recipe=selected_cache['recipe'])
 if spec.get('generated_substitutions'):
  from pipeline import boot_executable,digest
  if digest(boot_executable(checked['private']/'disc-root',profile))!=profile['expected_sha256']:raise ValueError('Generated substitution executable revision differs from the actual disc')
 abi_receipt=None
 if checked['abi_config'] is not None:
  from abi_layouts import audit
  abi_out=checked['private']/'native-source-abi'
  abi_report=audit(checked['abi_config'],abi_out)
  if abi_report['status']!='abi_layouts_match':raise ValueError('Shared source ABI layouts differ; native link blocked')
  abi_receipt=dict(path=str(abi_out/'receipt.json'),sha256=sha(abi_out/'receipt.json'),config=str(checked['abi_config']),config_sha256=sha(checked['abi_config']))
 requested=list(dict.fromkeys([spec['runner'],*spec['tests'],*spec.get('frameworks',{}).values()]))
 completed=spec.get('completed_build')
 if completed is not None:
  if not isinstance(completed,dict) or set(completed)!={'directory','receipt_sha256'}:raise ValueError('Declare pinned completed source build')
  if cache is not None:raise ValueError('Choose completed source build or source cache')
  out=path_in(checked['private'],completed['directory'])
  from clean_build import verify_completed_build
  report=verify_completed_build(checked['recipe'],out,completed['receipt_sha256'],checked['private'],requested)
 else:report=run(checked['recipe'],out,execute=True,only_targets=requested,**cache_options)
 # Merely compiling a generated file as an unused unit or test must not certify
 # a runner that never consumes it. Count only the runner's linked source graph.
 coverage=generated_coverage(checked,report)
 if out.is_dir():(out/'generated-coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
 if coverage['missing']:raise ValueError('Native graph did not compile all accepted generated sources: '+', '.join(Path(r['source']).name for r in coverage['missing'][:12]))
 artifacts=report['artifacts'];runner=artifacts[spec['runner']]
 return dict(source_cache_resolution=(selected_cache['hops'] if cache is not None else []),runtime_capabilities=runtime_capabilities(checked['recipe'],report,checked['runner_units']),success=True,adopted_completed_build=report.get('completed_build_verified',False),source_build_receipt=(dict(path=str((out/'receipt.json').relative_to(checked['private'])),sha256=sha(out/'receipt.json')) if report.get('status')=='source_build_passed' else None),source_lineage=declared.get('source_lineage',[]),incremental_build=report.get('incremental_build',False),fresh_compiled_units=report.get('fresh_compiled_units'),reused_verified_source_units=len(report.get('reused_verified_source_objects',{})),binary=runner['path'],sha256=runner['sha256'],runtime_verified=False,source_corrections=correction_receipt,translation_rebase=translation_receipt,
  source_graph_recipe_sha256=report['recipe_sha256'],source_graph_recipe=str(checked['recipe']),
  source_graph_staging=str(path_in(checked['private'],spec['staging'])),source_graph_staging_sha256=spec['staging_sha256'],historical_objects_used=False,
  runner_source_units=checked['runner_units'],runner_source_targets=checked['runner_targets'],
  generated_origin_resolution=checked['generated_origin_resolution'],
  abi_layouts_verified=abi_receipt is not None,abi_layouts_receipt=abi_receipt,
  frameworks={**{name:artifacts[target] for name,target in spec.get('frameworks',{}).items()},**checked['component_frameworks']},
  graph_tests={name:artifacts[name] for name in spec['tests']},graph_test_cases=checked['test_cases'],gameplay_verified=False)



def runtime_capabilities(recipe_path,report,runner_units):
 """Only advertise a service compiled into the runner with its source contract."""
 if 'native-separate-save-root' not in report.get('source_contracts',[]):return {}
 recipe_path=Path(recipe_path);recipe=json.loads(recipe_path.read_text())
 entries=[c for c in recipe.get('source_contracts',[]) if c.get('id')=='native-separate-save-root']
 if len(entries)!=1:raise ValueError('Save-root capability has no unique source contract')
 entry=entries[0];name=entry.get('file');unit=entry.get('unit')
 if unit not in runner_units or name not in report.get('dependencies',{}).get(unit,[]):raise ValueError('Save-root service was not compiled into the runner')
 file=path_in(recipe_path.parent,name)
 if sha(file)!=recipe['files'].get(name):raise ValueError('Save-root implementation changed')
 text=file.read_text();policy=(Path(__file__).parent/'runtime/host_environment/storage_paths.hpp').read_text().removeprefix('#pragma once\n')
 if policy not in text or any(f'{field}.mcRoot = ps2_port::memory_card_root({field}.elfDirectory);' not in text for field in ('defaults','normalized','paths')):raise ValueError('Save-root implementation does not match tested policy')
 return {'separate_save_root':True}


def verify_source_chain(stage,private):
 """Bind title amendments and qualified rule replay to the compiled graph."""
 expected=stage.get('source_graph_recipe_sha256');result={}
 build_record=stage.get('source_build_receipt')
 if build_record is not None:
  if not isinstance(build_record,dict) or set(build_record)!={'path','sha256'}:raise ValueError('Invalid source build receipt')
  path=path_in(Path(private).resolve(),build_record['path'])
  if sha(path)!=build_record['sha256']:raise ValueError('Source build receipt changed')
  record=json.loads(path.read_text())
  if record.get('status')!='source_build_passed' or record.get('recipe_sha256')!=expected or record.get('historical_objects_used') is not False:raise ValueError('Source build receipt does not cover graph')
  if not any(a.get('path')==stage.get('binary') and a.get('sha256')==stage.get('sha256') for a in record.get('artifacts',{}).values()):raise ValueError('Source build receipt does not cover runner')
  result['source_build_receipt']=record
 if stage.get('translation_rebase') is not None:
  from translation_graph import verify_receipt
  replay=verify_receipt(stage['translation_rebase'],private)
  if replay['graph']['sha256']!=expected:raise ValueError('Translation replay receipt does not cover this build')
  expected=replay['contract']['recipe_sha256'];result['translation_rebase']=replay
 if stage.get('source_corrections') is not None:
  from source_corrections import verify_native_receipt
  corrected=verify_native_receipt(stage['source_corrections'],private)
  if corrected['graph']['sha256']!=expected:raise ValueError('Source correction receipt does not cover this build')
  result['source_corrections']=corrected
  expected=corrected['contract']['recipe_sha256']
 lineage=stage.get('source_lineage',[])
 if not isinstance(lineage,list) or len(lineage)>16:raise ValueError('Invalid source lineage')
 for entry in reversed(lineage):
  if not isinstance(entry,dict) or set(entry)!={'kind','path','sha256'}:raise ValueError('Invalid source lineage entry')
  item=dict(path=entry['path'],sha256=entry['sha256'])
  if entry['kind']=='translation_rebase':
   from translation_graph import verify_receipt
   checked=verify_receipt(item,private)
  elif entry['kind']=='source_corrections':
   from source_corrections import verify_native_receipt
   checked=verify_native_receipt(item,private)
  else:raise ValueError('Unknown source lineage kind')
  if checked['graph']['sha256']!=expected:raise ValueError('Source lineage does not cover graph')
  expected=checked['contract']['recipe_sha256']
 result['source_lineage']=lineage
 return result


def framework_inputs(stage,private):
 private=Path(private).resolve();result={}
 artifacts=stage.get('frameworks',{})
 if not isinstance(artifacts,dict):raise ValueError('Framework receipt must be a mapping')
 for name,artifact in artifacts.items():
  if not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.dylib',name):raise ValueError('Invalid packaged framework name')
  p=Path(artifact['path'])
  if p.is_symlink() or not p.resolve().is_relative_to(private):raise ValueError('Framework must come from private source build storage')
  if not p.is_file() or sha(p)!=artifact.get('sha256'):raise ValueError('Framework differs from source build receipt')
  if artifact.get('component_receipt'):
   import native_components,pipeline as lab
   record=artifact['component_receipt'];receipt=Path(record['path'])
   if not receipt.resolve().is_relative_to(private) or sha(receipt)!=record['sha256']:raise ValueError('Packaged component receipt changed')
   verified=native_components.verify(lab,receipt)
   if verified!=artifact:raise ValueError('Packaged component provenance changed')
  if artifact.get('platform_sdk'):
   from platform_sdks import verify
   verify(artifact,private)
  result[name]=p
 return result
