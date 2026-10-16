"""Local compiler recipes and isolated source staging. Never executes recipe text."""
from pathlib import Path
import hashlib
import json
import re
import shutil

SCOPES = {'shared', 'title', 'enhancement'}
ROLES = {'source', 'script', 'config', 'object', 'binary', 'evidence'}
QUALIFICATIONS = {'synthetic', 'route', 'reported', 'unverified'}
COPY_ROLES = {'source', 'script', 'config'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value):
        raise ValueError('Invalid recipe/component/run identifier')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('Expected lowercase SHA-256')
    return value


def checked_input(item, roots):
    from pipeline import safe_relative, digest
    if item.get('role') not in ROLES or item.get('root') not in roots:
        raise ValueError('Unknown compiler input role or root')
    rel = safe_relative(item['path'])
    root = roots[item['root']].resolve()
    unresolved = root/rel
    path = unresolved.resolve()
    if unresolved.is_symlink() or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Compiler input escapes its root, is missing, or is a symlink')
    expected = sha(item['sha256'])
    if digest(path) != expected:
        raise ValueError('Compiler input changed: '+item['root']+'/'+str(rel))
    return dict(item, path=str(rel), resolved=str(path), bytes=path.stat().st_size)


def inspect_recipe(profile, private, recipe_path, roots=None, lock_path=None):
    import pipeline as lab
    private = Path(private).resolve()
    recipe_path = (private/recipe_path).resolve()
    if not recipe_path.is_relative_to(private) or not recipe_path.is_file():
        raise ValueError('Compiler recipe must live inside private game storage')
    raw = recipe_path.read_bytes()
    recipe = json.loads(raw)
    if recipe.get('schema') != 1 or recipe.get('title_id') != profile['id']:
        raise ValueError('Recipe schema/title differs from selected profile')
    identifier(recipe['id'])
    lock_path = Path(lock_path or lab.ROOT/'toolchain.lock.json')
    if lab.digest(lock_path) != sha(recipe['toolchain_lock_sha256']):
        raise ValueError('Compiler toolchain lock changed')
    roots = roots or {'work':lab.WORK, 'lab':lab.ROOT, 'game':private}
    host = recipe.get('host_toolchain')
    if host is not None:
        from toolchain import verify_host
        pin = checked_input(host, roots)
        if pin['role'] != 'config':
            raise ValueError('Host toolchain must be a pinned configuration')
        if not verify_host(json.loads(Path(pin['resolved']).read_text()))['matches']:
            raise ValueError('Selected host compiler/linker/SDK differs from recipe')
    elf = lab.boot_executable(private/'disc-root', profile)
    identity = lab.digest(elf)
    if identity != sha(recipe['executable_sha256']) or (
            profile.get('expected_sha256') and identity != profile['expected_sha256']):
        raise ValueError('Compiler recipe targets a different disc revision')
    components, seen, blockers = [], set(), []
    if not isinstance(recipe.get('components'), list) or not recipe['components']:
        raise ValueError('Compiler recipe needs components')
    for component in recipe['components']:
        name = identifier(component['id'])
        if name in seen:
            raise ValueError('Duplicate compiler component')
        seen.add(name)
        if component.get('scope') not in SCOPES or component.get('qualification') not in QUALIFICATIONS:
            raise ValueError('Unknown component scope/qualification')
        if type(component.get('clean_source')) is not bool:
            raise ValueError('Component clean_source must be an explicit boolean')
        if not isinstance(component.get('files'), list) or not component['files']:
            raise ValueError('Component needs pinned inputs')
        files = [checked_input(item, roots) for item in component['files']]
        closure = component.get('dependency_closure')
        if closure is not None:
            from dependencies import verify_files
            pin = checked_input(closure, roots)
            if pin['role'] != 'evidence':
                raise ValueError('Dependency closure must be pinned evidence')
            captured = json.loads(Path(pin['resolved']).read_text())
            if captured.get('status') != 'passed' or not captured.get('units'):
                raise ValueError('Dependency closure has not passed isolation checks')
            records = []
            for unit in captured['units']:
                if unit.get('reached_original_project_files') or unit.get('text_matches') is not True:
                    raise ValueError('Dependency closure isolation/object comparison failed')
                if not unit.get('inputs'):
                    raise ValueError('Dependency closure has no input inventory')
                for item in unit['inputs']:
                    if item.get('kind') not in {'project', 'toolchain'}:
                        raise ValueError('Unknown dependency ownership kind')
                    path = item.get('staged') or item['original']
                    if item.get('kind') == 'project' and not Path(path).resolve().is_relative_to(private):
                        raise ValueError('Staged project dependency escapes private storage')
                    records.append(dict(path=path, sha256=item['sha256']))
            if verify_files(records):
                raise ValueError('Frozen project or compiler/SDK dependency changed')
            component = dict(component, verified_dependency_files=len({r['path'] for r in records}))
        keys = [(item['root'], item['path']) for item in files]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate compiler input')
        reasons = []
        if not component['clean_source']:
            reasons.append('clean source reconstruction incomplete')
        if not any(item['role'] == 'source' for item in files):
            reasons.append('no pinned source input')
        if component['qualification'] in {'reported', 'unverified'}:
            reasons.append('independent conformance qualification pending')
        blockers.extend(dict(component=name, reason=reason) for reason in reasons)
        components.append(dict(component, files=files))
    return dict(local_only=True, recipe_id=recipe['id'], title_id=profile['id'],
        recipe_sha256=hashlib.sha256(raw).hexdigest(), executable_sha256=identity,
        toolchain_lock_sha256=lab.digest(lock_path), components=components,
        blockers=blockers, clean_build_ready=not blockers,
        scope='Pinned input/source readiness only. No C++ compilation, game launch, '
              'clean-build reproducibility or playable-game certification.')


def run_recipe(profile, private, recipe_path, name, stage=False):
    """Plan or freeze sources in a new private directory; no shell/AI execution."""
    import pipeline as lab
    identifier(name)
    private = Path(private).resolve()
    report = inspect_recipe(profile, private, recipe_path)
    target = private/'compiler-runs'/name
    if not target.resolve().is_relative_to(private):
        raise ValueError('Compiler run escapes private storage')
    target.mkdir(parents=True, exist_ok=False)
    report['mode'] = 'stage' if stage else 'plan'
    report['status'] = 'planned'
    report['staged_files'] = []
    try:
        if stage:
            for component in report['components']:
                for item in component['files']:
                    if item['role'] not in COPY_ROLES:
                        continue
                    rel = Path('sources')/component['id']/item['root']/item['path']
                    destination = target/rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item['resolved'], destination)
                    if lab.digest(destination) != item['sha256']:
                        raise ValueError('Input changed during source staging')
                    report['staged_files'].append(dict(path=str(rel), sha256=item['sha256']))
            report['status'] = 'staged' if report['clean_build_ready'] else 'staged_with_blockers'
        lab.save(target/'report.json', report)
    except Exception as error:
        report.update(status='failed', clean_build_ready=False, error=str(error))
        lab.save(target/'report.json', report)
        raise
    return report
