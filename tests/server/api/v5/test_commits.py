# Tests for the v5 commit endpoints.
#
# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     %{utils}/with_temporary_instance.py --db-version 5.0 %t.instance \
# RUN:         -- python %s %t.instance
# END.

import sys
import os
import unittest
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from v5_test_helpers import (
    create_app, create_client, admin_headers, make_scoped_headers,
    create_commit, create_run, collect_all_pages,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


class TestCommitList(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/commits."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_returns_200(self):
        resp = self.client.get(PREFIX + '/commits')
        self.assertEqual(resp.status_code, 200)

    def test_list_has_pagination_envelope(self):
        resp = self.client.get(PREFIX + '/commits')
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertIn('cursor', data)
        self.assertIn('next', data['cursor'])
        self.assertIn('previous', data['cursor'])

    def test_list_returns_commits(self):
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        rev = f'list-{uuid.uuid4().hex[:8]}'
        create_commit(session, ts, commit=rev)
        session.commit()
        session.close()

        resp = self.client.get(PREFIX + '/commits')
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        for item in data['items']:
            self.assertIn('commit', item)
            self.assertIn('ordinal', item)
            self.assertIn('fields', item)
            self.assertIsInstance(item['fields'], dict)

    def test_list_pagination(self):
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        for i in range(3):
            create_commit(session, ts,
                          commit=f'page-{uuid.uuid4().hex[:6]}-{i}')
        session.commit()
        session.close()

        resp = self.client.get(PREFIX + '/commits?limit=1')
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)
        self.assertIsNotNone(data['cursor']['next'])

        cursor = data['cursor']['next']
        resp2 = self.client.get(
            PREFIX + f'/commits?limit=1&cursor={cursor}')
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertEqual(len(data2['items']), 1)

    def test_invalid_cursor_returns_400(self):
        resp = self.client.get(
            PREFIX + '/commits?cursor=not-a-valid-cursor!!!')
        self.assertEqual(resp.status_code, 400)


class TestCommitSearch(unittest.TestCase):
    """Tests for the search parameter on GET /commits."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_search_by_commit_substring(self):
        unique = uuid.uuid4().hex[:8]
        middle = f'srch{unique}'
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        create_commit(session, ts, commit=f'aaa-{middle}-xxx')
        create_commit(session, ts, commit=f'bbb-{middle}-yyy')
        create_commit(session, ts, commit=f'other-{uuid.uuid4().hex[:8]}')
        session.commit()
        session.close()

        resp = self.client.get(PREFIX + f'/commits?search={middle}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 2)
        for item in data['items']:
            self.assertIn(middle, item['commit'])

    def test_search_case_insensitive(self):
        unique = uuid.uuid4().hex[:8]
        commit_val = f'CaSeCmT-{unique}'
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        create_commit(session, ts, commit=commit_val)
        session.commit()
        session.close()

        resp = self.client.get(
            PREFIX + f'/commits?search=casecmt-{unique}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        commits = [i['commit'] for i in data['items']]
        self.assertIn(commit_val, commits)

    def test_search_no_match(self):
        resp = self.client.get(
            PREFIX + '/commits?search=nonexistent-prefix-xyz')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()['items']), 0)


class TestCommitCreate(unittest.TestCase):
    """Tests for POST /api/v5/{ts}/commits."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_create_commit(self):
        rev = f'create-{uuid.uuid4().hex[:8]}'
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['commit'], rev)
        self.assertIsNone(data['ordinal'])

    def test_create_commit_with_ordinal(self):
        rev = f'ordinal-{uuid.uuid4().hex[:8]}'
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': rev, 'ordinal': 42},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['ordinal'], 42)

    def test_create_commit_with_fields(self):
        rev = f'fields-{uuid.uuid4().hex[:8]}'
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': rev,
                  'llvm_project_revision': 'abc123'},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['fields']['llvm_project_revision'], 'abc123')

    def test_create_duplicate_409(self):
        rev = f'dup-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 409)

    def test_create_no_auth_401(self):
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': 'no-auth'},
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_read_scope_403(self):
        headers = make_scoped_headers(self.app, 'read')
        resp = self.client.post(
            PREFIX + '/commits',
            json={'commit': 'read-only'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)


class TestCommitDetail(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/commits/{value}."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_get_detail(self):
        rev = f'detail-{uuid.uuid4().hex[:8]}'
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        create_commit(session, ts, commit=rev)
        session.commit()
        session.close()

        resp = self.client.get(PREFIX + f'/commits/{rev}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['commit'], rev)
        self.assertIn('ordinal', data)
        self.assertIn('fields', data)
        self.assertIn('previous_commit', data)
        self.assertIn('next_commit', data)

    def test_get_nonexistent_404(self):
        resp = self.client.get(
            PREFIX + '/commits/nonexistent-commit-xyz')
        self.assertEqual(resp.status_code, 404)

    def test_detail_with_neighbors(self):
        rev1 = f'nbr1-{uuid.uuid4().hex[:8]}'
        rev2 = f'nbr2-{uuid.uuid4().hex[:8]}'
        rev3 = f'nbr3-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev1, 'ordinal': 100},
            headers=admin_headers(),
        )
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev2, 'ordinal': 200},
            headers=admin_headers(),
        )
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev3, 'ordinal': 300},
            headers=admin_headers(),
        )

        resp = self.client.get(PREFIX + f'/commits/{rev2}')
        data = resp.get_json()
        self.assertIsNotNone(data['previous_commit'])
        self.assertIsNotNone(data['next_commit'])
        self.assertEqual(data['previous_commit']['commit'], rev1)
        self.assertEqual(data['next_commit']['commit'], rev3)


class TestCommitDetailETag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_etag_present(self):
        rev = f'etag-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.get(PREFIX + f'/commits/{rev}')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.headers.get('ETag'))
        self.assertTrue(resp.headers['ETag'].startswith('W/"'))

    def test_etag_304_on_match(self):
        rev = f'etag304-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.get(PREFIX + f'/commits/{rev}')
        etag = resp.headers['ETag']

        resp2 = self.client.get(
            PREFIX + f'/commits/{rev}',
            headers={'If-None-Match': etag},
        )
        self.assertEqual(resp2.status_code, 304)


class TestCommitUpdate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_set_ordinal(self):
        rev = f'setord-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.patch(
            PREFIX + f'/commits/{rev}',
            json={'ordinal': 9999},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['ordinal'], 9999)

    def test_patch_nonexistent_404(self):
        resp = self.client.patch(
            PREFIX + '/commits/nonexistent-xyz',
            json={'ordinal': 1},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 404)


class TestCommitDelete(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_delete_commit(self):
        rev = f'delc-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.delete(
            PREFIX + f'/commits/{rev}',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 204)

        resp = self.client.get(PREFIX + f'/commits/{rev}')
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_404(self):
        resp = self.client.delete(
            PREFIX + '/commits/nonexistent-del-xyz',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_with_regression_409(self):
        from v5_test_helpers import create_test, create_regression
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        c = create_commit(session, ts,
                          commit=f'reg-ref-{uuid.uuid4().hex[:8]}')
        c_commit = c.commit
        run = create_run(session, ts, c)
        t = create_test(session, ts,
                        name=f'reg-del/test/{uuid.uuid4().hex[:8]}')

        create_regression(
            session, ts,
            indicators=[{'run_id': run.id, 'test_id': t.id,
                         'metric': 'execution_time'}],
            commit=c)
        session.commit()
        session.close()

        resp = self.client.delete(
            PREFIX + f'/commits/{c_commit}',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 409)

    def test_delete_cascades_to_runs(self):
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        c = create_commit(session, ts,
                          commit=f'casc-{uuid.uuid4().hex[:8]}')
        c_commit = c.commit
        run = create_run(session, ts, c)
        run_uuid = run.uuid
        session.commit()
        session.close()

        self.client.delete(
            PREFIX + f'/commits/{c_commit}',
            headers=admin_headers(),
        )

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(resp.status_code, 404)


class TestCommitParamFilter(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/commits?param.X=Y."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

        db = cls.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        cls._tag = uuid.uuid4().hex[:8]

        cls.c_both = f'pf-both-{cls._tag}'
        cls.c_p1_only = f'pf-p1only-{cls._tag}'
        cls.c_p2_only = f'pf-p2only-{cls._tag}'
        cls.c_no_runs = f'pf-noruns-{cls._tag}'

        c_both = create_commit(session, ts, commit=cls.c_both)
        c_p1 = create_commit(session, ts, commit=cls.c_p1_only)
        c_p2 = create_commit(session, ts, commit=cls.c_p2_only)
        create_commit(session, ts, commit=cls.c_no_runs)

        create_run(session, ts, c_both,
                   run_parameters={'env': f'p1-{cls._tag}'})
        create_run(session, ts, c_both,
                   run_parameters={'env': f'p2-{cls._tag}'})
        create_run(session, ts, c_p1,
                   run_parameters={'env': f'p1-{cls._tag}'})
        create_run(session, ts, c_p2,
                   run_parameters={'env': f'p2-{cls._tag}'})
        session.commit()
        session.close()

    def _get_commits(self, **params):
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = PREFIX + '/commits'
        if qs:
            url += '?' + qs
        items = collect_all_pages(self, self.client, url)
        return [item['commit'] for item in items]

    def test_filter_by_param(self):
        """Only commits with runs matching param.env are returned."""
        commits = self._get_commits(**{f'param.env': f'p1-{self._tag}'})
        self.assertIn(self.c_both, commits)
        self.assertIn(self.c_p1_only, commits)
        self.assertNotIn(self.c_p2_only, commits)
        self.assertNotIn(self.c_no_runs, commits)

    def test_filter_by_other_param(self):
        commits = self._get_commits(**{f'param.env': f'p2-{self._tag}'})
        self.assertIn(self.c_both, commits)
        self.assertIn(self.c_p2_only, commits)
        self.assertNotIn(self.c_p1_only, commits)

    def test_param_combined_with_search(self):
        prefix = self.c_p1_only[:10]
        commits = self._get_commits(
            **{f'param.env': f'p1-{self._tag}', 'search': prefix})
        self.assertIn(self.c_p1_only, commits)
        self.assertNotIn(self.c_both, commits)


class TestCommitPagination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._commits = []
        db = cls.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        for i in range(5):
            rev = f'pag-{uuid.uuid4().hex[:8]}-{i}'
            create_commit(session, ts, commit=rev)
            cls._commits.append(rev)
        session.commit()
        session.close()

    def _collect_all_pages(self):
        url = PREFIX + '/commits?limit=2'
        return collect_all_pages(self, self.client, url)

    def test_pagination_collects_all_items(self):
        all_items = self._collect_all_pages()
        commits = [item['commit'] for item in all_items]
        for rev in self._commits:
            self.assertIn(rev, commits)

    def test_no_duplicate_items_across_pages(self):
        all_items = self._collect_all_pages()
        commits = [item['commit'] for item in all_items]
        self.assertEqual(len(commits), len(set(commits)))


class TestCommitSortOrdinal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

        cls.c1 = f'so-c1-{uuid.uuid4().hex[:8]}'
        cls.c2 = f'so-c2-{uuid.uuid4().hex[:8]}'
        cls.c3 = f'so-c3-{uuid.uuid4().hex[:8]}'
        cls.c_no_ord = f'so-noord-{uuid.uuid4().hex[:8]}'

        cls.ord1 = 500010
        cls.ord2 = 500050
        cls.ord3 = 500100

        cls.client.post(PREFIX + '/commits',
                        json={'commit': cls.c1, 'ordinal': cls.ord1},
                        headers=admin_headers())
        cls.client.post(PREFIX + '/commits',
                        json={'commit': cls.c2, 'ordinal': cls.ord2},
                        headers=admin_headers())
        cls.client.post(PREFIX + '/commits',
                        json={'commit': cls.c3, 'ordinal': cls.ord3},
                        headers=admin_headers())
        cls.client.post(PREFIX + '/commits',
                        json={'commit': cls.c_no_ord},
                        headers=admin_headers())

    def test_sort_ordinal_order(self):
        url = PREFIX + '/commits?sort=ordinal'
        items = collect_all_pages(self, self.client, url)
        commits = [item['commit'] for item in items]
        idx1 = commits.index(self.c1)
        idx2 = commits.index(self.c2)
        idx3 = commits.index(self.c3)
        self.assertLess(idx1, idx2)
        self.assertLess(idx2, idx3)

    def test_sort_ordinal_excludes_null(self):
        url = PREFIX + '/commits?sort=ordinal'
        items = collect_all_pages(self, self.client, url)
        commits = [item['commit'] for item in items]
        self.assertNotIn(self.c_no_ord, commits)

    def test_invalid_sort_returns_422(self):
        resp = self.client.get(PREFIX + '/commits?sort=bogus')
        self.assertEqual(resp.status_code, 422)

    def test_sort_ordinal_with_param(self):
        """sort=ordinal combines with param.* filter."""
        tag = uuid.uuid4().hex[:8]
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        c1_obj = ts.get_commit(session, commit=self.c1)
        c3_obj = ts.get_commit(session, commit=self.c3)
        self.assertIsNotNone(c1_obj)
        self.assertIsNotNone(c3_obj)
        create_run(session, ts, c1_obj,
                   run_parameters={'sortenv': tag})
        create_run(session, ts, c3_obj,
                   run_parameters={'sortenv': tag})
        session.commit()
        session.close()

        url = PREFIX + f'/commits?sort=ordinal&param.sortenv={tag}'
        items = collect_all_pages(self, self.client, url)
        commits = [item['commit'] for item in items]
        self.assertIn(self.c1, commits)
        self.assertIn(self.c3, commits)
        self.assertNotIn(self.c2, commits)
        self.assertNotIn(self.c_no_ord, commits)
        self.assertLess(commits.index(self.c1), commits.index(self.c3))


class TestCommitUnknownParams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/commits?bogus=1')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('bogus', data['error']['message'])

    def test_detail_unknown_param_returns_400(self):
        rev = f'unk-{uuid.uuid4().hex[:8]}'
        self.client.post(
            PREFIX + '/commits',
            json={'commit': rev},
            headers=admin_headers(),
        )
        resp = self.client.get(PREFIX + f'/commits/{rev}?bogus=1')
        self.assertEqual(resp.status_code, 400)


class TestCommitResolve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def _create(self, commit, **kwargs):
        body = {'commit': commit, **kwargs}
        return self.client.post(
            PREFIX + '/commits', json=body, headers=admin_headers())

    def _resolve(self, commits, headers=None):
        kw = {'json': {'commits': commits}}
        if headers is not None:
            kw['headers'] = headers
        return self.client.post(PREFIX + '/commits/resolve', **kw)

    def test_resolve_basic(self):
        rev1 = f'res-{uuid.uuid4().hex[:8]}'
        rev2 = f'res-{uuid.uuid4().hex[:8]}'
        self._create(rev1, llvm_project_revision='sha-aaa')
        self._create(rev2, llvm_project_revision='sha-bbb')

        resp = self._resolve([rev1, rev2])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('results', data)
        self.assertIn('not_found', data)
        self.assertEqual(len(data['results']), 2)

    def test_resolve_not_found(self):
        rev = f'res-nf-{uuid.uuid4().hex[:8]}'
        self._create(rev)
        missing = f'res-missing-{uuid.uuid4().hex[:8]}'

        resp = self._resolve([rev, missing])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['not_found'], [missing])

    def test_resolve_empty_list_422(self):
        resp = self._resolve([])
        self.assertEqual(resp.status_code, 422)

    def test_resolve_deduplicates(self):
        rev = f'res-dup-{uuid.uuid4().hex[:8]}'
        self._create(rev)

        resp = self._resolve([rev, rev, rev])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['results']), 1)


class TestCommitTag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def _create(self, commit, **kwargs):
        body = {'commit': commit, **kwargs}
        return self.client.post(
            PREFIX + '/commits', json=body, headers=admin_headers())

    def _patch(self, commit, **kwargs):
        return self.client.patch(
            PREFIX + f'/commits/{commit}',
            json=kwargs, headers=admin_headers())

    def test_tag_null_on_creation(self):
        rev = f'tag-null-{uuid.uuid4().hex[:8]}'
        resp = self._create(rev)
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.get_json()['tag'])

    def test_set_tag_via_patch(self):
        rev = f'tag-set-{uuid.uuid4().hex[:8]}'
        self._create(rev)
        resp = self._patch(rev, tag='release-18')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['tag'], 'release-18')

    def test_clear_tag_via_patch(self):
        rev = f'tag-clr-{uuid.uuid4().hex[:8]}'
        self._create(rev)
        self._patch(rev, tag='release-18')
        resp = self._patch(rev, tag=None)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()['tag'])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
