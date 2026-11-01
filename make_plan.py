"""Recover declarative source/archive targets from a Make dry run.

No transcript command is ever executed. Compiler and archive lines are parsed;
other build-system operations are reported separately and cannot enter recipes.
"""
from pathlib import Path
import json,shlex
from compile_recipe import settings,DRIVERS
from clean_build import sha,ID


def recover(transcript,directory):
 path=Path(transcript).resolve();cwd=Path(directory).resolve();units=[];archives=[];blocked=[];unexecuted=[];outputs={}
 for line_number,line in enumerate(path.read_text().splitlines(),1):
  if not line.strip():continue
  try:args=shlex.split(line)
  except ValueError as e:blocked.append(dict(line=line_number,error=str(e)));continue
  if not args:continue
  driver=Path(args[0]).name
  if driver not in DRIVERS|{'ar'}:
   unexecuted.append(dict(line=line_number,command=line));continue
  try:
   if any(a in {';','&&','||','|','>','>>','<','&'} or '$' in a or '`' in a or '\n' in a for a in args):raise ValueError('Shell expressions cannot enter a compiler/archive recipe')
   if driver=='ar':
    if len(args)<4 or args[1] not in {'rc','rcs','cr','crs'}:raise ValueError('Only source archive creation is supported')
    archives.append(dict(line=line_number,output=str((cwd/args[2]).resolve()),inputs=[str((cwd/p).resolve()) for p in args[3:]]));continue
   normalized=[args[0]];i=1
   while i<len(args):
    a=args[i];i+=1
    if a in {'-MMD','-MD','-MP'}:continue
    if a in {'-MF','-MT','-MQ'}:
     if i>=len(args):raise ValueError('Missing compiler dependency-output value')
     i+=1;continue
    normalized.append(a)
   unit=settings(dict(directory=str(cwd),arguments=normalized));output=unit.pop('output')
   if output in outputs:raise ValueError('Repeated compiler output requires explicit resolution')
   unit['id']='unit_'+str(len(units));outputs[output]=unit['id'];units.append(unit)
  except (ValueError,TypeError) as e:blocked.append(dict(line=line_number,error=str(e),command=line))
 targets=[];names=set()
 for archive in archives:
  try:
   name=Path(archive['output']).stem
   if not ID.fullmatch(name) or name.casefold() in names:raise ValueError('Invalid/duplicate archive target')
   if any(p not in outputs for p in archive['inputs']):raise ValueError('Archive references an object without a recovered source compile')
   names.add(name.casefold());targets.append(dict(id=name,kind='static',units=[outputs[p] for p in archive['inputs']]))
  except ValueError as e:blocked.append(dict(line=archive['line'],error=str(e)))
 if not units or not targets:blocked.append(dict(error='No complete source/archive build plan found'))
 return dict(schema=1,status='make_plan_recovered' if not blocked else 'make_plan_blocked',transcript=str(path),transcript_sha256=sha(path),directory=str(cwd),units=units,targets=targets,blocked=blocked,commands_not_executed=unexecuted,historical_objects_used=False,dependency_closure_verified=False,equivalence_verified=False,gameplay_verified=False)
