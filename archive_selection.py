"""Select archive source candidates from independently pinned build controls.

This produces a source selection report, never copies historical objects. Native
text matches are explicitly weaker than whole-object or runtime equivalence.
"""
from pathlib import Path
import json
from clean_build import inspect,sha
from archive_sources import members


def select(audit_path,recipe_path,build_path,controls_path,candidates_path,destination):
 paths=[Path(p).resolve() for p in [audit_path,recipe_path,candidates_path,controls_path]]
 audit_file,recipe_file,candidates_file,controls_file=paths
 pins={str(p):sha(p) for p in paths}
 audit=json.loads(audit_file.read_text());spec=json.loads(candidates_file.read_text());control=json.loads(controls_file.read_text())
 checked=inspect(recipe_file);build=Path(build_path).resolve();receipt_file=build/'receipt.json';receipt=json.loads(receipt_file.read_text());pins[str(receipt_file)]=sha(receipt_file)
 out=Path(destination).absolute()
 if out.exists() or out.is_symlink():raise ValueError('Preserve prior source selection reports')
 if receipt.get('status') not in {'source_build_passed','source_units_built'} or receipt.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Require a completed build of this pinned recipe')
 if control.get('status') not in {'controls_match','controls_mismatch'} or control.get('recipe_sha256')!=checked['recipe_sha256'] or control.get('build_receipt_sha256')!=sha(receipt_file):raise ValueError('Control report is stale or incomplete')
 archive=Path(audit['archive'])
 if sha(archive)!=audit.get('archive_sha256') or members(archive)!=[{k:m[k] for k in ['name','bytes','sha256','ordinal']} for m in audit['members']]:raise ValueError('Archive differs from its audited inventory')
 pins[str(archive.resolve())]=sha(archive)
 if spec.get('schema')!=1 or not isinstance(spec.get('candidates'),list):raise ValueError('Declare archive candidate units')
 rows=control.get('objects',[]);controls={r['unit']:r for r in rows}
 if len(controls)!=len(rows):raise ValueError('Duplicate unit in control report')
 units={u['id'] for u in checked['recipe']['units']};seen=set();by_member={}
 for c in spec['candidates']:
  unit=c.get('unit');ordinal=c.get('ordinal')
  if unit not in units or type(ordinal) is not int or not 0<=ordinal<len(audit['members']) or (ordinal,unit) in seen:raise ValueError('Unknown or duplicate archive candidate')
  seen.add((ordinal,unit));row=controls.get(unit)
  if row is None:raise ValueError('Candidate lacks an independent object control')
  obj=build/'obj'/(unit+'.o')
  if not obj.is_file() or sha(obj)!=receipt.get('objects',{}).get(unit) or sha(obj)!=row.get('object_sha256'):raise ValueError('Compiled candidate changed after controls')
  member=audit['members'][ordinal]
  if row.get('reference_sha256')!=member['sha256']:raise ValueError('Control reference does not represent this archive member')
  exact=row['object_sha256']==member['sha256']
  if row.get('exact_object_bytes') is not exact:raise ValueError('Inconsistent exact-object control')
  text=row.get('native_text_match') is True
  if text and (control.get('native_text_requested') is not True or not row.get('built_text_sha256') or row.get('built_text_sha256')!=row.get('reference_text_sha256') or row.get('built_text_bytes')!=row.get('reference_text_bytes')):raise ValueError('Inconsistent native-text control')
  strength='exact_object_bytes' if exact else ('native_text_only' if text else None)
  by_member.setdefault(ordinal,[]).append(dict(unit=unit,strength=strength))
 selected=[];unresolved=[]
 for m in audit['members']:
  candidates=by_member.get(m['ordinal'],[]);exact=[c for c in candidates if c['strength']=='exact_object_bytes'];matches=exact or [c for c in candidates if c['strength']=='native_text_only']
  row=dict(ordinal=m['ordinal'],member=m['name'],member_sha256=m['sha256'])
  if len(matches)==1:selected.append(dict(row,**matches[0]))
  else:unresolved.append(dict(row,reason='multiple_matching_sources' if matches else 'no_matching_source',candidates=candidates))
 for path,pin in pins.items():
  if sha(path)!=pin:raise ValueError('Selection input changed during analysis')
 result=dict(schema=1,status='source_candidates_selected' if not unresolved else 'source_selection_blocked',input_pins=pins,selected=selected,unresolved=unresolved,selected_units=[r['unit'] for r in selected],complete_member_coverage=not unresolved,historical_objects_linked=False,equivalence_verified=False,gameplay_verified=False,scope='Archive source candidates only; native-text matches exclude data/relocation/link/runtime acceptance')
 with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
 return result
