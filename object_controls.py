"""Compare isolated source-built objects against pinned historical controls.

Historical objects are analysis inputs only and never enter a build/link graph.
Text fingerprints are explicitly weaker than full data/relocation equivalence.
"""
from compute_slot import exclusive
from pathlib import Path
import hashlib,json,shutil,subprocess
from clean_build import sha,inspect,ID,object_binary


@exclusive('object-controls',when='native')
def compare(recipe,build,references,destination,*,native=False,slot_check=True):
 checked=inspect(recipe);build=Path(build).resolve();receipt_path=build/'receipt.json';receipt=json.loads(receipt_path.read_text())
 if receipt.get('recipe_sha256')!=checked['recipe_sha256'] or receipt.get('status') not in {'source_units_built','source_build_passed'}:raise ValueError('Require a completed source build of this pinned recipe')
 reference_path=Path(references).resolve();raw=reference_path.read_bytes();spec=json.loads(raw)
 if spec.get('schema')!=1 or not isinstance(spec.get('objects'),list) or not spec['objects']:raise ValueError('Declare pinned object controls')
 out=Path(destination).resolve()
 if out.exists() or out.is_relative_to(build) or out.is_relative_to(checked['root']):raise ValueError('Use a new separate control directory')
 selected=[];seen=set()
 for r in spec['objects']:
  name=r.get('unit','');ref=Path(r['reference']);obj=build/'obj'/(name+'.o')
  if not ID.fullmatch(name) or name in seen or name not in receipt.get('objects',{}):raise ValueError('Control must select distinct completed source units')
  seen.add(name)
  if not ref.is_absolute() or ref.is_symlink() or (ref.suffix not in {'.o','.bc'} and not object_binary(ref)) or not ref.is_file():raise ValueError('Reference must be a real pinned object file')
  if ref.resolve()==obj.resolve():raise ValueError('A rebuilt object cannot be its own independent control')
  if sha(ref)!=r.get('sha256') or sha(obj)!=receipt['objects'][name]:raise ValueError('Source or reference object differs from its pin')
  selected.append((name,obj,ref,r['sha256']))
 compiler=shutil.which('clang')
 if native:
  if not compiler:raise ValueError('Native text controls need Clang')
  if slot_check:
   processes=subprocess.check_output(['/bin/ps','-axo','comm='],text=True)
   if any(Path(p.strip()).name in {'ps2EntryRunner','ninja','clang','clang++','blender','realesrgan-ncnn-vulkan'} for p in processes.splitlines()):raise RuntimeError('Game/build/render slot is occupied')
 out.mkdir(parents=True)
 report=dict(schema=1,status='running',recipe_sha256=checked['recipe_sha256'],build_receipt_sha256=sha(receipt_path),references_sha256=hashlib.sha256(raw).hexdigest(),objects=[],native_text_requested=native,equivalence_verified=False,gameplay_verified=False,scope='Object-byte and optional native-text controls only; data/relocations/full-link/runtime acceptance remain separate')
 def save():(out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 save()
 try:
  for name,obj,ref,pin in selected:
   row=dict(unit=name,object_sha256=sha(obj),reference_sha256=pin,exact_object_bytes=sha(obj)==pin,native_text_match=None)
   if native:
    from source_freeze import native_text
    a=native_text(compiler,obj,out/(name+'.built.native.o'),out);b=native_text(compiler,ref,out/(name+'.reference.native.o'),out)
    row.update(native_text_match=a==b,built_text_bytes=len(a),reference_text_bytes=len(b),built_text_sha256=hashlib.sha256(a).hexdigest(),reference_text_sha256=hashlib.sha256(b).hexdigest())
    if a!=b:
     from object_symbols import differences
     built_native=out/(name+'.built.native.o');reference_native=out/(name+'.reference.native.o')
     try:row['text_symbol_differences']=differences(built_native if built_native.exists() else obj,reference_native if reference_native.exists() else ref)
     except ValueError as error:row['text_symbol_analysis_unavailable']=str(error)
   if sha(obj)!=receipt['objects'][name] or sha(ref)!=pin:raise ValueError('Object changed during comparison')
   report['objects'].append(row);save()
  if sha(receipt_path)!=report['build_receipt_sha256'] or sha(reference_path)!=report['references_sha256']:raise ValueError('Control inputs changed during comparison')
  exact=all(r['exact_object_bytes'] for r in report['objects']);text=all(r['native_text_match'] for r in report['objects']) if native else None
  report.update(status='controls_match' if (text if native else exact) else 'controls_mismatch',all_exact_object_bytes=exact,all_native_text_match=text)
  save();return report
 except Exception as error:
  report.update(status='failed',error=str(error));save();raise
