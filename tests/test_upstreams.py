import unittest
import upstreams


class Upstreams(unittest.TestCase):
    def test_metadata_records_recent_release_without_activating_it(self):
        def get(path):
            if '?' in path and '/commits?' in path:return []
            if '/releases?' in path:return [dict(published_at='2026-10-22T00:00:00Z',tag_name='v1',html_url='https://github.com/o/r/releases/tag/v1',body='upstream claim')]
            if '/commits/' in path:return dict(sha='a'*40,commit={'committer':{'date':'2026-10-22T00:00:00Z'}})
            return dict(default_branch='main',html_url='https://github.com/o/r',created_at='2026-10-20T00:00:00Z',license={'spdx_id':'MIT'})
        r=upstreams.collect(['o/r'],since='2026-10-17T00:00:00Z',until='2026-10-25T00:00:00Z',getter=get)['repositories'][0]
        self.assertEqual(r['releases'][0]['tag'],'v1');self.assertFalse(r['locally_qualified'])
        self.assertFalse(r['creation_is_publication_proof']);self.assertFalse(r['source_inspected'])

    def test_invalid_windows_and_repository_injection_rejected(self):
        for name in ('o/r/../../x','https://github.com/o/r','o/r?token=secret'):
            with self.assertRaises(ValueError):upstreams.collect([name],since='2026-10-17T00:00:00Z',until='2026-10-25T00:00:00Z')
        with self.assertRaises(ValueError):upstreams.collect([],since='2026-10-17',until='2026-10-25')

    def test_unavailable_source_is_explicit_not_empty_success(self):
        def get(path):raise ValueError('unavailable')
        r=upstreams.collect(['o/r'],since='2026-10-17T00:00:00Z',until='2026-10-25T00:00:00Z',getter=get)
        self.assertEqual(r['repositories'][0]['status'],'unavailable')
