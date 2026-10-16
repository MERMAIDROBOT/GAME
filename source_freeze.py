"""Recover an isolated compile unit from its real compiler-discovered dependencies.

Runs known compiler tooling with argument arrays. Never executes recipe shell
text or game output. A passed unit is not a source-complete game build.
"""
from pathlib import Path
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import pipeline as lab
from dependencies import dependency_paths


def compile_arguments(entry, source, output):
    command=list(entry.get('arguments') or shlex.split(entry['command']))
    compiler=Path(command[0]).resolve()
    approved={Path(p).resolve() for n in ['clang','clang++','cc','c++','g++','gcc'] if (p:=shutil.which(n))}
    if compiler not in approved:
        raise ValueError('Unknown compiler driver')
    if '-c' not in command or '-o' not in command or command.count('-o')!=1:
        raise ValueError('Expected one compiler-only output')
    if any(a.startswith(('@','-fplugin','-Xclang','-load','-include-pch','-fmodules')) for a in command[1:]):
        raise ValueError('Unsupported response/plugin/module compile inputs')
    original=Path(entry['file']).resolve()
    # Accept a structured command, not make fragments or shell expansion.
    if Path(command[-1]).resolve()!=original: raise ValueError('Unrecognized compile database source position')
    command[0]=str(compiler);command[-1]=str(Path(source).resolve())
    command[command.index('-o')+1]=str(Path(output).resolve())
    command[1:1]=['-iquote',str(original.parent)]
    return command


def remap(command, project, snapshot):
    result=[]
    for token in command:
        prefix=''
        value=token
        for option in ['-I','-iquote','-isystem']:
            if token.startswith(option) and token!=option:
                prefix,value=option,token[len(option):];break
        # Normalize before staging: a missing intermediate directory in a
        # lexical `a/../b` include path otherwise breaks the isolated rebuild.
        p=Path(os.path.normpath(value))
        if p.is_absolute() and p.is_relative_to(project) and not p.is_relative_to(snapshot.parent):
            value=str(snapshot/p.relative_to(project))
        result.append(prefix+value)
    return result


def invoke(command,directory,log):
    with Path(log).open('w') as stream:
        subprocess.run(command,cwd=directory,stdout=stream,stderr=subprocess.STDOUT,check=True)


def native_text(compiler, path, target, directory):
    data=Path(path).read_bytes()
    native=Path(path)
    if data[:4] in {b'BC\xc0\xde',b'\xde\xc0\x17\x0b'}:
        native=Path(target)
        invoke([compiler,'-x','ir','-c',str(path),'-o',str(native)],directory,native.with_suffix('.log'))
    if platform.system()=='Darwin':
        output=subprocess.check_output(['/usr/bin/otool','-s','__TEXT','__text',str(native)],text=True)
        lines=output.splitlines()[2:]
    else:
        objdump=shutil.which('objdump')
        if not objdump: raise ValueError('Native text inspection needs objdump')
        output=subprocess.check_output([objdump,'-s','-j','.text',str(native)],text=True)
        lines=output.splitlines()
    # Extract only complete hex groups, ignoring file headings, addresses and ASCII.
    chunks=[]
    for line in lines:
        fields=line.split()
        if len(fields)<2 or not re.fullmatch(r'[0-9a-fA-F]+',fields[0]): continue
        groups=[]
        for word in (fields[1:] if platform.system()=='Darwin' else fields[1:5]):
            if not re.fullmatch(r'[0-9a-fA-F]{2,8}',word) or len(word)%2: break
            groups.append(word)
        chunks.extend(bytes.fromhex(word) for word in groups)
    text=b''.join(chunks)
    if not text: raise ValueError('No native text section found')
    return text


def freeze(profile,private,compile_db,unit,source,reference,name):
    from compiler import identifier
    identifier(name)
    private=Path(private).resolve();project=lab.WORK.resolve()
    target=private/'compiler-runs'/name
    if target.exists(): raise ValueError('Use a new isolated freeze name')
    db,source,reference=map(lambda p:Path(p).resolve(),(compile_db,source,reference))
    if any(not p.is_relative_to(project) or not p.is_file() for p in [db,source,reference]):
        raise ValueError('Freeze inputs must be files under local tool/game work storage')
    entries=[e for e in json.loads(db.read_text()) if e['file'].endswith(unit)]
    if len(entries)!=1: raise ValueError('Compile-unit selector must match exactly once')
    entry=entries[0];target.mkdir(parents=True)
    report=dict(schema=1,local_only=True,status='failed',title_id=profile['id'],unit=unit,source=str(source),
        source_sha256=lab.digest(source),reference=str(reference),reference_sha256=lab.digest(reference),
        compile_database_sha256=lab.digest(db),whole_game_clean_build=False)
    lab.save(target/'report.json',report)
    try:
        probe=target/'probe.o';dep=target/'probe.d'
        command=compile_arguments(entry,source,probe)
        # All relative include paths resolve as they did in the original database.
        directory=Path(entry['directory']).resolve()
        command+=['-MD','-MF',str(dep),'-MT','probe']
        invoke(command,directory,target/'probe-build.log')
        dependencies=dependency_paths(dep.read_text(),directory)
        if source not in dependencies: raise ValueError('Compiler did not report selected source input')
        snapshot=target/'snapshot';snapshot.mkdir()
        records=[]
        for path in dependencies:
            record=dict(original=str(path),sha256=lab.digest(path),kind='toolchain')
            if path.is_relative_to(project):
                destination=snapshot/path.relative_to(project)
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
                if lab.digest(destination)!=record['sha256']: raise ValueError('Frozen source copy differs')
                record.update(kind='project',staged=str(destination))
            records.append(record)
        frozen=target/'frozen.o';frozen_dep=target/'frozen.d'
        rebuild=remap(command,project,snapshot)
        rebuild[rebuild.index('-o')+1]=str(frozen)
        rebuild[rebuild.index('-MF')+1]=str(frozen_dep)
        invoke(rebuild,target,target/'frozen-build.log')
        reached=dependency_paths(frozen_dep.read_text(),target)
        escaped=[str(p) for p in reached if p.is_relative_to(project) and not p.is_relative_to(snapshot)]
        if escaped: raise ValueError('Frozen compile still reads original project dependencies')
        expected_paths={Path(r.get('staged') or r['original']) for r in records}
        if set(reached)!=expected_paths: raise ValueError('Frozen dependency closure changed')
        compiler=command[0]
        a=native_text(compiler,probe,target/'probe.native.o',target)
        b=native_text(compiler,frozen,target/'frozen.native.o',target)
        c=native_text(compiler,reference,target/'reference.native.o',target)
        for record in records:
            if lab.digest(record['original'])!=record['sha256']: raise ValueError('Dependency changed while freezing')
        report.update(inputs=records,capture_command=command,rebuild_command=rebuild,
            compiler_sha256=lab.digest(compiler),reached_original_project_files=escaped,
            probe_object_sha256=lab.digest(probe),frozen_object_sha256=lab.digest(frozen),
            probe_object_exact_reference=lab.digest(probe)==lab.digest(reference),
            probe_matches_reference=a==c,text_matches=a==b,
            native_text_sha256=__import__('hashlib').sha256(a).hexdigest(),
            status='passed' if a==b==c else 'text_mismatch',
            scope='One isolated compiler dependency closure and native text comparison. Not all data/relocations, full clean link, ABI parity or gameplay certification.')
    except Exception as error:
        report['error']=str(error);lab.save(target/'report.json',report);raise
    lab.save(target/'report.json',report)
    return report
