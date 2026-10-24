"""Public-source update discovery. Metadata informs review, never activates code."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import re
import ssl
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')


def fetch(path):
    if not path.startswith('/') or '..' in path or '://' in path:
        raise ValueError('Expected public GitHub API path')
    request = Request('https://api.github.com'+path, headers={
        'Accept':'application/vnd.github+json', 'User-Agent':'ps2-port-lab-upstream-review'})
    trust = Path('/etc/ssl/cert.pem')
    context = ssl.create_default_context(cafile=str(trust) if sys.platform=='darwin' and trust.is_file() else None)
    with urlopen(request, timeout=20, context=context) as response:
        raw = response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise ValueError('Upstream response too large')
    return json.loads(raw)


def collect(repositories, *, since, until, getter=fetch):
    start = datetime.fromisoformat(since.replace('Z','+00:00'))
    end = datetime.fromisoformat(until.replace('Z','+00:00'))
    if start.tzinfo is None or end.tzinfo is None or end<=start or end-start>timedelta(days=31):
        raise ValueError('Use a timezone-aware window of at most 31 days')
    if len(repositories)>32: raise ValueError('At most 32 repositories per review')
    results=[]
    for name in dict.fromkeys(repositories):
        if not REPOSITORY.fullmatch(name): raise ValueError('Invalid GitHub repository')
        try:
            meta=getter('/repos/'+name)
            branch=meta['default_branch']
            # Branch names are URL-encoded; never use input as a shell command.
            from urllib.parse import quote
            head=getter('/repos/'+name+'/commits/'+quote(branch,safe=''))
            commits=getter('/repos/'+name+'/commits?since='+quote(since,safe='')+'&until='+quote(until,safe='')+'&per_page=100')
            releases=getter('/repos/'+name+'/releases?per_page=100')
            recent=[]
            for release in releases:
                date=release.get('published_at')
                if date and start<=datetime.fromisoformat(date.replace('Z','+00:00'))<end:
                    recent.append(dict(tag=release['tag_name'],published_at=date,url=release['html_url'],notes=release.get('body','')))
            results.append(dict(repository=name,url=meta['html_url'],created_at=meta['created_at'],
                creation_is_publication_proof=False,license=(meta.get('license') or {}).get('spdx_id'),
                head=head['sha'],head_date=head['commit']['committer']['date'],
                commits=[dict(sha=c['sha'],date=c['commit']['committer']['date'],subject=c['commit']['message'].splitlines()[0],url=c['html_url']) for c in commits],
                commits_truncated=len(commits)==100,releases=recent,releases_truncated=len(releases)==100,
                status='review_required',source_inspected=False,locally_qualified=False))
        except (HTTPError,URLError,KeyError,ValueError) as error:
            results.append(dict(repository=name,status='unavailable',error=str(error)))
    report=dict(schema=1,since=since,until=until,repositories=results,
        policy='No code download, execution, automatic dependency upgrade or gameplay claim. Review source/license and qualify independently.')
    report['sha256']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    return report
