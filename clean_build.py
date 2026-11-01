"""Build a pinned source graph without historical object/archive inputs.

Recipes select fixed compiler/archive/link operations, never shell commands.
Sources, headers and generated shader headers must be staged and hashed first.
A successful build is source closure evidence, not retail gameplay acceptance.
"""
from compute_slot import exclusive,assert_idle
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import threading

ID=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}\Z')
FLAGS={'-O0','-O1','-O2','-O3','-Os','-fPIC','-fno-strict-aliasing','-fvisibility=hidden',
       '-fvisibility-inlines-hidden','-fno-exceptions','-fno-rtti','-ffast-math',
       '-fno-fast-math','-ffp-contract=off','-ffp-contract=fast','-fwrapv','-fno-omit-frame-pointer',
       '-flto=thin','-pthread','-Wno-everything','-fblocks','-Wall','-Wextra','-Wpedantic',
       '-Werror=implicit-function-declaration','-Werror=pointer-arith','-Wno-unused-command-line-argument','-Wno-deprecated-declarations','-Winvalid-pch'}
FLAGS.update({'-g','-mdynamic-no-pic','-fno-common','-fomit-frame-pointer','-fno-math-errno','-fno-signed-zeros','-mstack-alignment=16','-Qunused-arguments',
 '-Werror=partial-availability','-Wdeclaration-after-statement','-Wdisabled-optimization','-Wpointer-arith','-Wredundant-decls','-Wwrite-strings','-Wtype-limits','-Wundef','-Wmissing-prototypes','-Wstrict-prototypes','-Wempty-body','-Wno-parentheses','-Wno-switch','-Wno-format-zero-length','-Wno-pointer-sign','-Wno-unused-const-variable','-Wno-bool-operation','-Wno-char-subscripts','-Werror=missing-prototypes','-Werror=return-type'})
STANDARDS={'c99','c11','c17','gnu99','gnu11','gnu17','c++11','c++14','c++17','c++20','gnu++17','gnu++20'}
LINK_FLAGS={'-pthread','-Wl,-search_paths_first','-O0','-O1','-O2','-O3','-Os','-flto=thin','-fno-lto','-Wl,-headerpad_max_install_names','-Wl,-dead_strip'}
SDK_LIBRARIES={'c++','m','z','bz2','iconv','pthread','dl','objc'}
FRAMEWORKS={'IOSurface','AppKit','Foundation','Cocoa','IOKit','CoreVideo','CoreAudio','AudioToolbox','Metal','QuartzCore','OpenGL','CoreFoundation','CoreGraphics','AVFoundation','VideoToolbox','CoreMedia','Security'}


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()


def object_binary(path):
    p=Path(path)
    if not p.is_file():return False
    with p.open('rb') as f:header=f.read(20)
    if header.startswith((b'BC\xc0\xde',b'\xde\xc0\x17\x0b')):return True
    if header[:4]==b'\x7fELF':
        # ET_REL object only; ET_EXEC and ET_DYN cannot be reused as units.
        if len(header)<20 or header[4] not in {1,2} or header[5] not in {1,2} or header[6]!=1:return False
        return int.from_bytes(header[16:18], 'little' if header[5]==1 else 'big')==1
    if len(header)<16:return False
    if header[:4] in {b'\xcf\xfa\xed\xfe',b'\xce\xfa\xed\xfe'}:return int.from_bytes(header[12:16],'little')==1
    if header[:4] in {b'\xfe\xed\xfa\xcf',b'\xfe\xed\xfa\xce'}:return int.from_bytes(header[12:16],'big')==1
    return False


def compiled_binary(path):
    p=Path(path)
    if not p.is_file():return False
    with p.open('rb') as f:magic=f.read(8)
    from source_inventory import compiled_magic
    return compiled_magic(magic)


def relative(value):
    if not isinstance(value,str) or not value or '\\' in value:
        raise ValueError('Expected a nonempty relative source path')
    p=Path(value)
    if p.is_absolute() or '..' in p.parts or p==Path('.'):
        raise ValueError('Source paths must stay inside the staged root')
    return p


def path_in(root,value):
    p=root/relative(value)
    if any(q.is_symlink() for q in [p,*p.parents] if q.is_relative_to(root)) or not p.resolve().is_relative_to(root):
        raise ValueError('Source symlinks/escapes are not accepted: '+str(value))
    return p


def filesystem_metadata(path):
    path=Path(path)
    if not path.name.startswith('._') or path.is_symlink():return False
    companion=path.with_name(path.name[2:])
    if not companion.exists() or companion.is_symlink() or not (companion.is_file() or companion.is_dir()):return False
    with path.open('rb') as stream:return stream.read(8)==b'\x00\x05\x16\x07\x00\x02\x00\x00'


def inspect(recipe_path):
    recipe_path=Path(recipe_path).resolve();root=recipe_path.parent
    recipe=json.loads(recipe_path.read_text())
    if recipe.get('schema')!=1 or recipe.get('source_only') is not True:
        raise ValueError('An explicit source-only schema 1 recipe is required')
    files=recipe.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('Source inventory is empty')
    from source_inventory import verify_files,audit_include
    verify_files(root,files)
    pinned_directories={str(parent) for name in files for parent in Path(name).parents if str(parent)!='.'}
    metadata_companions=set(files) | pinned_directories
    units=recipe.get('units',[]);targets=recipe.get('targets',[])
    unit_ids=set();target_ids=set();unit_names_folded=set();target_names_folded=set();validated_includes=set()
    for u in units:
        name=u.get('id','')
        if not ID.fullmatch(name) or name.casefold() in unit_names_folded:raise ValueError('Invalid or duplicate unit id')
        if name in {'receipt','link'} or name.startswith('link-'): raise ValueError('Reserved unit id')
        unit_ids.add(name);unit_names_folded.add(name.casefold())
        source=u.get('source','');path_in(root,source)
        if source not in files or Path(source).suffix not in {'.c','.cc','.cpp','.cxx','.mm','.m','.S'}:
            raise ValueError('Unit source is not pinned source code: '+source)
        if u.get('standard','c++20') not in STANDARDS:raise ValueError('Unsupported language standard')
        if any(f not in FLAGS for f in u.get('flags',[])):raise ValueError('Unknown compiler flag')
        for d in u.get('defines',[]):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:=[A-Za-z0-9_./:+-]+)?',d):raise ValueError('Invalid compiler define')
        if type(u.get('sdk_framework_search',False)) is not bool:raise ValueError('SDK framework search must be boolean')
        ordered=u.get('include_paths',[])
        if not isinstance(ordered,list) or any(not isinstance(x,dict) or x.get('kind') not in {'-I','-isystem','-iquote'} for x in ordered):raise ValueError('Invalid ordered include paths')
        for folder in [*u.get('includes',[]),*(x['path'] for x in ordered)]:
            p=path_in(root,folder)
            if not p.is_dir():raise ValueError('Missing include directory')
            if p in validated_includes:continue
            validated_includes.add(p)
            # Descriptor walks retain the same pin/metadata policy without
            # restatting every ancestor for each included file.
            audit_include(root,folder,files,metadata_companions)
        for header in u.get('force_include',[]):
            if header not in files:raise ValueError('Unpinned forced include')
            path_in(root,header)
    for t in targets:
        name=t.get('id','')
        if not ID.fullmatch(name) or name.casefold() in target_names_folded:raise ValueError('Invalid or duplicate target id')
        if name.casefold() in {'obj','logs','receipt','receipt.json'}:raise ValueError('Reserved build target id')
        target_ids.add(name);target_names_folded.add(name.casefold())
        if t.get('kind')=='static' and t.get('uses'): raise ValueError('Static archives must list source units, not nested archives')
        if t.get('kind') not in {'static','shared','executable'}:raise ValueError('Unsupported target kind')
        if not t.get('units') and not t.get('uses'):raise ValueError('Empty build target')
        if any(x not in unit_ids for x in t.get('units',[])):raise ValueError('Target has an unknown source unit')
        if any(x not in LINK_FLAGS for x in t.get('link_flags',[])):raise ValueError('Unknown linker flag')
        if t.get('kind')=='static' and t.get('link_flags'):raise ValueError('Static archive cannot carry linker flags')
        if any(x not in SDK_LIBRARIES for x in t.get('libraries',[])):raise ValueError('Only declared SDK libraries are accepted')
        if any(x not in FRAMEWORKS for x in t.get('frameworks',[])):raise ValueError('Unknown SDK framework')
        if any(p not in {'@executable_path/../Frameworks','@loader_path'} for p in t.get('rpaths',[])):raise ValueError('Only bundle-relative runtime library paths are accepted')
        install=t.get('install_name')
        if install and not re.fullmatch(r'@rpath/[A-Za-z0-9_.-]+\.dylib',install):raise ValueError('Unsafe install name')
    order=[];visiting=set();visited=set();by_id={t['id']:t for t in targets}
    def visit(n):
        if n not in by_id:raise ValueError('Missing target dependency: '+n)
        if n in visiting:raise ValueError('Cyclic target dependencies')
        if n in visited:return
        visiting.add(n)
        for d in by_id[n].get('uses',[]):visit(d)
        visiting.remove(n);visited.add(n);order.append(n)
    for n in by_id:visit(n)
    if not units or not targets:raise ValueError('Build graph is empty')
    # Explicit carryforward obligations survive source replacement/amendment.
    # This is a literal implementation/link check, not runtime equivalence proof.
    contract_ids=validate_source_contracts(recipe,lambda name:path_in(root,name).read_bytes().decode('utf8',errors='surrogateescape'))
    return dict(recipe=recipe,root=root,order=order,recipe_sha256=sha(recipe_path),
                source_files=len(files),source_contracts=sorted(contract_ids),clean_source_inputs=True,gameplay_verified=False)


def compiler_header_roots(compiler,language,env):
    """Ask the selected compiler for its built-in search roots, never inherited -I."""
    result=subprocess.run([compiler,'-E','-x',language,'-','-v'],input='',text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,check=True)
    roots=[];inside=False
    for line in result.stderr.splitlines():
        if line.strip()=='#include <...> search starts here:':inside=True;continue
        if line.strip()=='End of search list.':inside=False
        if not inside:continue
        value=line.strip().removesuffix(' (framework directory)')
        p=Path(value)
        if not p.is_absolute() or not p.is_dir():raise ValueError('Unexpected compiler system header root')
        roots.append(p.resolve())
    if not roots:raise ValueError('Compiler did not disclose system header search roots')
    return roots


def classify_dependencies(deps,root,files,system_roots):
    root=Path(root).resolve();system_roots=[Path(r).resolve() for r in system_roots]
    project=[];toolchain={}
    for p in deps:
        p=Path(p).resolve()
        if p.is_relative_to(root):
            relative_path=str(p.relative_to(root))
            if relative_path not in files:raise RuntimeError('Compiler reached an unstaged project dependency: '+str(p))
            project.append(relative_path)
        elif any(p.is_relative_to(r) for r in system_roots):toolchain[str(p)]=sha(p)
        else:raise RuntimeError('Compiler reached an unstaged project dependency: '+str(p))
    return project,toolchain


def failure_diagnostics(text):
    """Classify native link failures without guessing a replacement implementation."""
    if 'No space left on device' in text or 'not enough space' in text.lower():return dict(kind='storage_exhausted')
    missing=[];active=False
    for line in text.splitlines():
        if line.startswith('Undefined symbols for architecture '):active=True;continue
        if active and line.startswith('ld:'):active=False
        match=re.match(r'\s{2}"(.+)", referenced from:',line) if active else None
        if match:missing.append(match.group(1))
        unix=re.search(r"undefined reference to [`'](.+)'",line)
        if unix:missing.append(unix.group(1))
    if missing:return dict(kind='source_symbol_closure_missing',undefined_symbols=list(dict.fromkeys(missing)))
    if 'duplicate symbol' in text or 'multiple definition of' in text:return dict(kind='duplicate_source_providers')
    if 'framework not found' in text or 'library not found' in text:return dict(kind='missing_link_dependency')
    return dict(kind='compiler_or_linker_failure')


def direct_source_cache(build,previous_recipe,private,*,receipt_sha256,recipe_sha256):
    """Select the pinned directly compiled ancestor, never reuse chained objects."""
    private=Path(private).resolve();seen=set();hops=[]
    def confined(value):
        if not isinstance(value,(str,Path)) or not str(value):raise ValueError("Source cache ancestry needs a valid path")
        path=Path(value)
        if not path.is_absolute():path=private/path
        try:relative_path=str(path.relative_to(private))
        except ValueError:raise ValueError('Source cache ancestry escapes private storage') from None
        return path_in(private,relative_path)
    receipt=confined(Path(build)/'receipt.json');recipe=confined(previous_recipe)
    for _ in range(16):
        if not all(isinstance(pin,str) and re.fullmatch('[a-f0-9]{64}',pin) for pin in (receipt_sha256,recipe_sha256)):
            raise ValueError('Source cache ancestry needs exact input pins')
        if receipt in seen:raise ValueError('Cyclic source cache ancestry')
        seen.add(receipt)
        if sha(receipt)!=receipt_sha256 or sha(recipe)!=recipe_sha256:raise ValueError('Source cache ancestry pin changed')
        old=json.loads(receipt.read_text())
        if not isinstance(old,dict):raise ValueError('Source cache ancestry receipt must be an object')
        if old.get('schema')!=1 or old.get('historical_objects_used') is not False or old.get('recipe_sha256')!=recipe_sha256 or old.get('status') not in {'source_build_passed','source_units_built','failed'}:
            raise ValueError('Source cache ancestry is not a finished source build')
        hops.append(dict(receipt=str(receipt),sha256=receipt_sha256,recipe=str(recipe),recipe_sha256=recipe_sha256))
        if not old.get('reused_verified_source_objects'):
            return dict(build=receipt.parent,recipe=recipe,hops=hops)
        parent=old.get('source_cache_receipt')
        if not isinstance(parent,dict) or set(parent)!={'path','sha256','recipe','recipe_sha256'}:
            raise ValueError('Chained cache has no pinned direct-source ancestry')
        receipt=confined(parent['path']);recipe=confined(parent['recipe'])
        if receipt.name!='receipt.json':raise ValueError('Source cache ancestry must name a build receipt')
        receipt_sha256=parent['sha256'];recipe_sha256=parent['recipe_sha256']
    raise ValueError('Source cache ancestry exceeds16builds')


def verified_source_cache(recipe_path, checked, build, previous_recipe, tools, compiler, roots):
    """Reuse only objects compiled by this builder from unchanged pinned sources.

    This is an explicit incremental build, never fresh-build acceptance. The
    complete set of relative file paths must stay identical, preserving include
    resolution. Each reused unit and every recorded dependency must be unchanged.
    Relocated/edited graphs are incremental evidence, never a fresh build.
    """
    prior=inspect(previous_recipe)
    if Path(build).is_symlink():raise ValueError('Unsafe source cache path')
    directory=Path(build).resolve()
    receipt=directory/'receipt.json'
    if directory.is_symlink() or receipt.is_symlink():raise ValueError('Unsafe source cache path')
    old=json.loads(receipt.read_text())
    if old.get('schema')!=1 or old.get('historical_objects_used') is not False or old.get('recipe_sha256')!=prior['recipe_sha256']:
        raise ValueError('Source cache is not bound to the previous source recipe')
    if old.get('status') not in {'source_build_passed','source_units_built','failed'}:raise ValueError('Source cache build has not finished')
    if old.get('reused_verified_source_objects'):
        raise ValueError('Reuse a directly compiled source receipt, not a chained cache')
    if set(prior['recipe']['files'])!=set(checked['recipe']['files']):
        raise ValueError('Source cache requires the same complete source inventory paths')
    if old.get('compiler_tools')!=tools or old.get('compiler')!=compiler or old.get('system_header_roots')!=[str(p) for p in roots]:
        raise ValueError('Source cache compiler/toolchain differs')
    for path,digest in old.get('toolchain_headers',{}).items():
        if not Path(path).is_file() or sha(path)!=digest:raise ValueError('Source cache SDK/header changed: '+path)
    previous={u['id']:u for u in prior['recipe']['units']};result={}
    for unit in checked['recipe']['units']:
        name=unit['id']
        if previous.get(name)!=unit or name not in old.get('objects',{}):continue
        deps=old.get('dependencies',{}).get(name,[])
        if any(prior['recipe']['files'].get(d)!=checked['recipe']['files'].get(d) for d in deps):continue
        obj=directory/'obj'/(name+'.o');deps=old.get('dependencies',{}).get(name,[])
        if obj.is_symlink() or not obj.is_file() or sha(obj)!=old['objects'][name]:raise ValueError('Source cache object changed: '+name)
        if not deps or unit['source'] not in deps or any(d not in checked['recipe']['files'] for d in deps):
            raise ValueError('Source cache dependency closure missing: '+name)
        result[name]=dict(path=str(obj),sha256=old['objects'][name],dependencies=deps)
    return result,dict(path=str(receipt),sha256=sha(receipt),recipe=str(Path(previous_recipe).resolve()),recipe_sha256=prior['recipe_sha256']),old.get('toolchain_headers',{})


@exclusive('source-build',when='execute')
def run(recipe_path,destination,*,jobs=2,execute=False,slot_check=True,only_units=None,only_targets=None,reuse_build=None,reuse_recipe=None):
    checked=inspect(recipe_path);root=checked['root'];recipe=checked['recipe'];out=Path(destination).resolve()
    if type(jobs) is not int or jobs<1 or jobs>2:raise ValueError('Use one or two compiler workers')
    if out.exists() or out.is_relative_to(root):raise ValueError('Use a new build directory outside the staged source root')
    if (reuse_build is None)!=(reuse_recipe is None):raise ValueError('Provide both source cache build and its pinned recipe')
    selected=recipe['units']
    order=checked['order']
    if only_targets is not None:
        if only_units is not None:raise ValueError('Select targets or units, not both')
        targets={t['id']:t for t in recipe['targets']};reachable=set()
        if not only_targets or any(n not in targets for n in only_targets):raise ValueError('Select existing source target IDs')
        def visit(name):
            if name in reachable:return
            reachable.add(name)
            for dependency in targets[name].get('uses',[]):visit(dependency)
        for name in only_targets:visit(name)
        order=[n for n in order if n in reachable]
        names={u for n in order for u in targets[n].get('units',[])}
        selected=[u for u in selected if u['id'] in names]
    if only_units is not None:
        names=set(only_units)
        if not names or any(n not in {u['id'] for u in selected} for n in names):
            raise ValueError('Select existing source unit IDs')
        selected=[u for u in selected if u['id'] in names]
    report=dict(schema=1,status='planned',recipe_sha256=checked['recipe_sha256'],source_files=checked['source_files'],
                historical_objects_used=False,gameplay_verified=False,source_contracts=checked['source_contracts'],targets=order if only_units is None else [],selected_units=[u['id'] for u in selected])
    if not execute:return report
    if slot_check:assert_idle()
    cc=shutil.which('clang');cxx=shutil.which('clang++');ar=shutil.which('ar')
    if not cc or not cxx or not ar:raise ValueError('Clang and ar are required')
    # Do not inherit injected include/library paths or game/runtime toggles.
    env={k:v for k,v in os.environ.items() if k not in {'CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','DYLD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES','SDKROOT','MACOSX_DEPLOYMENT_TARGET','COMPILER_PATH','GCC_EXEC_PREFIX','CCC_OVERRIDE_OPTIONS'} and not k.startswith(('PS2_PORT_','PS2X_'))}
    sdk=None
    if any(u.get('sdk_framework_search') for u in selected):
        sdk=Path(subprocess.check_output(['/usr/bin/xcrun','--show-sdk-path'],text=True,env=env).strip())
        if not sdk.is_absolute() or not (sdk/'System/Library/Frameworks').is_dir():raise ValueError('Active SDK framework directory missing')
    system_roots=list(dict.fromkeys(compiler_header_roots(cc,'c',env)+compiler_header_roots(cxx,'c++',env)))
    available=shutil.disk_usage(out.parent).free
    if available<128*1024*1024:raise RuntimeError('Build storage has less than128MiB available; select storage with free space before compiling')
    out.mkdir();(out/'obj').mkdir();(out/'logs').mkdir();(out/'tmp').mkdir()
    # Large disc ports can exhaust the system disk with compiler/LTO temporaries.
    # Keep them with the explicitly selected build output (including USB builds).
    env['TMPDIR']=str(out/'tmp');env['TMP']=str(out/'tmp');env['TEMP']=str(out/'tmp')
    report.update(status='running',compiler=subprocess.check_output([cxx,'--version'],text=True).splitlines()[0],started=time.time(),
        compiler_tools={str(Path(p).resolve()):sha(Path(p).resolve()) for p in [cc,cxx,ar]},
        system_header_roots=[str(p) for p in system_roots],commands={})
    cache={};cache_headers={}
    if reuse_build is not None:
        cache,pin,cache_headers=verified_source_cache(recipe_path,checked,reuse_build,reuse_recipe,report['compiler_tools'],report['compiler'],system_roots)
        report.update(source_cache_receipt=pin,incremental_build=True,reused_verified_source_objects={})
    report_lock=threading.Lock()
    def save():
        with report_lock:
            temporary=out/'receipt.next.json'
            temporary.write_text(json.dumps(report,indent=2)+'\n');temporary.replace(out/'receipt.json')
    def command(args,name):
        with report_lock:report['commands'][name]=[str(a) for a in args]
        log=out/'logs'/(name+'.log')
        with log.open('w') as f:
            result=subprocess.run(args,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT)
        if result.returncode:
            diagnostics=failure_diagnostics(log.read_text(errors='replace')[-256000:])
            with report_lock:report['failure']=dict(stage=name,log=str(log),exit_code=result.returncode,**diagnostics)
            raise RuntimeError('Source build failed at '+name+' ('+diagnostics['kind']+'); see '+str(log))
    def unit(u):
        if u['id'] in cache:
            c=cache[u['id']]
            return u['id'],c['sha256'],c['dependencies'],cache_headers,c['path'],True
        source=root/u['source'];obj=out/'obj'/(u['id']+'.o');dep=out/'obj'/(u['id']+'.d')
        compiler=cc if source.suffix in {'.c','.m','.S'} else cxx
        args=[compiler,*([] if source.suffix=='.S' else ['-std='+u.get('standard','c++20')]),'-MD','-MF',str(dep),'-c',str(source),'-o',str(obj)]
        args+=u.get('flags',[])
        args+=['-D'+x for x in u.get('defines',[])]
        args+=['-I'+str(root/x) for x in u.get('includes',[])]
        for inc in u.get('include_paths',[]):args.extend([inc['kind'],str(root/inc['path'])])
        if u.get('sdk_framework_search'):args+=['-F'+str(sdk/'System/Library/Frameworks')]
        for h in u.get('force_include',[]):args+=['-include',str(root/h)]
        command(args,u['id'])
        from dependencies import dependency_paths
        deps=dependency_paths(dep.read_text(),root)
        project,toolchain=classify_dependencies(deps,root,recipe['files'],system_roots)
        return u['id'],sha(obj),project,toolchain,str(obj),False
    try:
        save()
        report.update(objects={},dependencies={},toolchain_headers={},compiled_units=0,fresh_compiled_units=0,total_units=len(selected))
        object_paths={}
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures=[pool.submit(unit,u) for u in selected]
            try:
                for future in concurrent.futures.as_completed(futures):
                    name,digest,deps,headers,object_path,reused=future.result()
                    object_paths[name]=object_path
                    if reused:report['reused_verified_source_objects'][name]=cache[name]
                    else:report['fresh_compiled_units']+=1
                    report['objects'][name]=digest;report['dependencies'][name]=deps
                    for path,digest in headers.items():
                        previous=report['toolchain_headers'].setdefault(path,digest)
                        if previous!=digest:raise RuntimeError('Toolchain header changed during build: '+path)
                    report['compiled_units']+=1
                    if report['compiled_units']%10==0 or report['compiled_units']==len(selected):save()
            except BaseException:
                # Ctrl-C must cancel queued work before the executor waits for
                # its active compiler workers to finish.
                for pending in futures:pending.cancel()
                raise
        for contract in recipe.get('source_contracts',[]):
            unit_id=contract['unit']
            if unit_id in report['dependencies'] and contract.get('file') and contract['file'] not in report['dependencies'][unit_id]:
                raise RuntimeError('Required source contract file was not consumed by its unit: '+contract['id'])
        for path,digest in report['toolchain_headers'].items():
            if sha(path)!=digest:raise RuntimeError('Toolchain header changed during build: '+path)
        for path,digest in report['compiler_tools'].items():
            if sha(path)!=digest:raise RuntimeError('Compiler tool changed during build: '+path)
        if only_units is not None:
            inspect(recipe_path)
            if sha(recipe_path)!=checked['recipe_sha256']:raise RuntimeError('Source recipe changed during build')
            report.update(status='source_units_built',finished=time.time(),equivalence_verified=False)
            save();return report
        built={};targets={t['id']:t for t in recipe['targets']}
        for name in order:
            t=targets[name];suffix={'static':'.a','shared':'.dylib','executable':''}[t['kind']]
            target=out/(name+suffix);inputs=[object_paths[u] for u in t.get('units',[])]+[str(built[x]) for x in t.get('uses',[])]
            if t['kind']=='static':args=[ar,'rcs',str(target),*inputs]
            else:
                args=[cxx,*t.get('link_flags',[]),*inputs,'-o',str(target)]
                if t['kind']=='shared':args+=['-dynamiclib']
                if t.get('install_name'):args+=['-install_name',t['install_name']]
                for rpath in t.get('rpaths',[]):args+=['-Wl,-rpath,'+rpath]
                args+=['-l'+x for x in t.get('libraries',[])]
                for f in t.get('frameworks',[]):args+=['-framework',f]
            command(args,'link-'+name);built[name]=target
        for name,cached in report.get('reused_verified_source_objects',{}).items():
            if sha(cached['path'])!=cached['sha256']:raise RuntimeError('Source cache object changed during build: '+name)
        # Rehash the entire source inventory after compiling, not just before.
        inspect(recipe_path)
        if sha(recipe_path)!=checked['recipe_sha256']:
            raise RuntimeError('Source recipe changed during build')
        report.update(status='source_build_passed',artifacts={n:dict(path=str(p),sha256=sha(p)) for n,p in built.items()},finished=time.time())
        save();return report
    except BaseException as error:
        cancelled=isinstance(error,(KeyboardInterrupt,SystemExit))
        report.update(status='cancelled' if cancelled else 'failed',error=str(error) or type(error).__name__,finished=time.time());save();raise


def verify_completed_build(recipe_path, directory, expected_pin, private, only_targets):
    """Adopt an unchanged source-built result after a later wrapper gate failed.

    No commands execute and no output is replaced. Verify the complete source,
    compiler/SDK, source-cache chain, object and linked-artifact records again.
    """
    private=Path(private).resolve();directory=Path(directory)
    if directory.is_symlink() or not directory.resolve().is_relative_to(private):raise ValueError('Unsafe completed source build directory')
    directory=directory.resolve();receipt=directory/'receipt.json'
    if receipt.is_symlink() or sha(receipt)!=expected_pin:raise ValueError('Completed source build receipt changed')
    report=json.loads(receipt.read_text());checked=inspect(recipe_path);recipe=checked['recipe']
    if report.get('schema')!=1 or report.get('status')!='source_build_passed' or report.get('historical_objects_used') is not False or report.get('recipe_sha256')!=checked['recipe_sha256']:raise ValueError('Completed source build does not cover this source graph')
    targets={t['id']:t for t in recipe['targets']};wanted=set()
    def visit(name):
        if name in wanted:return
        if name not in targets:raise ValueError('Completed build target is unknown')
        wanted.add(name)
        for child in targets[name].get('uses',[]):visit(child)
    if not only_targets:raise ValueError('Completed build needs explicit targets')
    for name in only_targets:visit(name)
    units={u for n in wanted for u in targets[n].get('units',[])}
    if set(report.get('selected_units',[]))!=units or set(report.get('targets',[]))!=wanted or set(report.get('objects',{}))!=units or set(report.get('dependencies',{}))!=units:raise ValueError('Completed source build omits requested units or targets')
    tools=report.get('compiler_tools',{});headers=report.get('toolchain_headers',{})
    if not tools or not isinstance(headers,dict) or not isinstance(report.get('system_header_roots'),list):raise ValueError('Completed source build has no compiler/header provenance')
    for name,pin in {**tools,**headers}.items():
        p=Path(name)
        if not p.is_file() or sha(p)!=pin:raise ValueError('Completed compiler or SDK input changed: '+name)
    reused=report.get('reused_verified_source_objects',{});eligible={}
    if reused:
        entry=report.get('source_cache_receipt',{})
        source=Path(entry.get('path',''));prior=Path(entry.get('recipe',''))
        if source.is_symlink() or prior.is_symlink() or not source.resolve().is_relative_to(private) or not prior.resolve().is_relative_to(private) or sha(source)!=entry.get('sha256') or sha(prior)!=entry.get('recipe_sha256'):raise ValueError('Completed source-cache provenance changed')
        eligible,_,_=verified_source_cache(recipe_path,checked,source.parent,prior,tools,report['compiler'],[Path(p) for p in report['system_header_roots']])
    definitions={u['id']:u for u in recipe['units']}
    for name,pin in report['objects'].items():
        deps=report['dependencies'][name]
        if not deps or definitions[name]['source'] not in deps or any(p not in recipe['files'] for p in deps):raise ValueError('Completed source dependency closure is invalid')
        if name in reused:
            if eligible.get(name)!=reused[name] or reused[name]['dependencies']!=deps or reused[name]['sha256']!=pin:raise ValueError('Completed reused source object is not verified')
            obj=Path(reused[name]['path'])
        else:obj=directory/'obj'/(name+'.o')
        if obj.is_symlink() or not obj.resolve().is_relative_to(private) or not obj.is_file() or sha(obj)!=pin or not object_binary(obj):raise ValueError('Completed source object changed: '+name)
    if report.get('fresh_compiled_units')!=len(units)-len(reused):raise ValueError('Completed source compilation count changed')
    artifacts=report.get('artifacts',{})
    if set(artifacts)!=wanted:raise ValueError('Completed linked target inventory differs')
    for name,item in artifacts.items():
        p=Path(item['path']);suffix={'static':'.a','shared':'.dylib','executable':''}[targets[name]['kind']]
        if p.is_symlink() or p.resolve()!=directory/(name+suffix) or not p.is_file() or sha(p)!=item['sha256']:raise ValueError('Completed linked artifact changed: '+name)
    if sha(receipt)!=expected_pin:raise ValueError('Completed source build receipt changed during verification')
    return dict(report,completed_build_verified=True)


def validate_source_contracts(recipe,read_source):
    """Check linked implementation declarations against actual or planned source bytes."""
    files=recipe['files'];units=recipe['units'];by_id={t['id']:t for t in recipe['targets']}
    contracts=recipe.get('source_contracts',[])
    if not isinstance(contracts,list):raise ValueError('Source contracts must be a list')
    contract_ids=set();units_by_id={u['id']:u for u in units}
    for contract in contracts:
        if not isinstance(contract,dict) or not {'id','unit','target','required_fragments'}<=set(contract) or set(contract)-{'id','unit','target','required_fragments','file','header_basename','header_reference'}:
            raise ValueError('Invalid source contract declaration')
        name=contract['id'];unit=contract['unit'];target=contract['target'];fragments=contract['required_fragments']
        if not isinstance(name,str) or not ID.fullmatch(name) or name in contract_ids:
            raise ValueError('Invalid or duplicate source contract id')
        contract_ids.add(name)
        if not isinstance(unit,str) or unit not in units_by_id or not isinstance(target,str) or target not in by_id:
            raise ValueError('Source contract requires known unit and target')
        linked=set()
        def collect(n):
            linked.update(by_id[n].get('units',[]))
            for child in by_id[n].get('uses',[]):collect(child)
        collect(target)
        if unit not in linked:raise ValueError('Source contract unit is not linked into its required target: '+name)
        if not isinstance(fragments,list) or not fragments or any(not isinstance(f,str) or not f or len(f)>1048576 for f in fragments):
            raise ValueError('Source contract requires bounded literal fragments')
        contract_file=contract.get('file',units_by_id[unit]['source'])
        if not isinstance(contract_file,str) or contract_file not in files:raise ValueError('Source contract file must be a pinned input')
        if 'header_basename' in contract or 'header_reference' in contract:
            basename=contract.get('header_basename');reference=contract.get('header_reference')
            if not isinstance(basename,str) or Path(basename).name!=basename or not isinstance(reference,str) or reference not in files or Path(reference).name!=basename:
                raise ValueError('Invalid header family source contract')
            expected=read_source(reference)
            family=[f for f in files if Path(f).name==basename]
            if any(read_source(f)!=expected for f in family):
                raise ValueError('Conflicting header declarations in source contract: '+name)
        source=read_source(contract_file)
        if any(source.count(fragment)!=1 for fragment in fragments):
            raise ValueError('Required source implementation missing or ambiguous: '+name)
    return contract_ids
