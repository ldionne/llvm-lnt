# Tests for the v5 regression endpoints.
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
    create_app, create_client, make_scoped_headers,
    collect_all_pages, submit_run, submit_regression,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


def _triage_headers(app):
    return make_scoped_headers(app, 'triage')


def _setup_regression_with_indicators(client, num_indicators=2,
                                      state='active', commit=None):
    """Create a regression with indicators via the API.

    Returns (regression_uuid, [indicator_uuid, ...]).
    """
    tag = uuid.uuid4().hex[:8]
    tests = [f'reg/test/{tag}/{i}' for i in range(num_indicators)]
    commit_str = commit or f'reg-rev-{tag}'

    # Submit a run so the commit, tests, and run exist
    run_data = submit_run(client, commit_str,
                          [{'name': t, 'execution_time': [1.0 + i]}
                           for i, t in enumerate(tests)],
                          run_parameters={'env': f'reg-{tag}'})
    run_uuid = run_data['run_uuid']

    indicators = [
        {'run_uuid': run_uuid, 'test': t, 'metric': 'execution_time'}
        for t in tests
    ]
    reg = submit_regression(client, indicators=indicators,
                            state=state, commit=commit_str)
    indicator_uuids = [ind['uuid'] for ind in reg['indicators']]
    return reg['uuid'], indicator_uuids


# ==========================================================================
# Regression List Tests
# ==========================================================================

def _find_in_list(items, uuid):
    """Find an item by UUID in a list response's items array."""
    for r in items:
        if r['uuid'] == uuid:
            return r
    return None


class TestRegressionList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_returns_200_with_envelope(self):
        resp = self.client.get(PREFIX + '/regressions')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertIsInstance(data['items'], list)
        self.assertIn('cursor', data)
        self.assertIn('next', data['cursor'])
        self.assertIn('previous', data['cursor'])

    def test_list_item_has_expected_fields(self):
        reg_uuid, _ = _setup_regression_with_indicators(self.client, 1)
        resp = self.client.get(PREFIX + '/regressions?limit=500')
        data = resp.get_json()
        item = _find_in_list(data['items'], reg_uuid)
        self.assertIsNotNone(item)
        self.assertIn('uuid', item)
        self.assertIn('title', item)
        self.assertIn('bug', item)
        self.assertIn('state', item)
        self.assertIn('commit', item)
        self.assertIn('run_count', item)
        self.assertIn('test_count', item)
        # List items should NOT have indicators embedded
        self.assertNotIn('indicators', item)
        # No machine_count in the new model
        self.assertNotIn('machine_count', item)

    def test_list_item_run_and_test_counts(self):
        """Create a regression with 2 indicators (1 run, 2 tests).
        Verify run_count == 1 and test_count == 2."""
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 2)
        resp = self.client.get(PREFIX + '/regressions?limit=500')
        data = resp.get_json()
        item = _find_in_list(data['items'], reg_uuid)
        self.assertIsNotNone(item)
        self.assertEqual(item['run_count'], 1)
        self.assertEqual(item['test_count'], 2)

    def test_list_filter_by_state(self):
        _setup_regression_with_indicators(self.client, 1)
        resp = self.client.get(PREFIX + '/regressions?state=active')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for r in data['items']:
            self.assertEqual(r['state'], 'active')

    def test_list_filter_by_state_multiple(self):
        tag = uuid.uuid4().hex[:8]
        test1 = f'state-test/{tag}/1'
        test2 = f'state-test/{tag}/2'
        commit_str = f'state-rev-{tag}'

        run_data = submit_run(self.client, commit_str, [
            {'name': test1, 'execution_time': [1.0]},
            {'name': test2, 'execution_time': [1.0]},
        ])
        run_uuid = run_data['run_uuid']

        submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test1,
                         'metric': 'execution_time'}],
            state='active', commit=commit_str)
        submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test2,
                         'metric': 'execution_time'}],
            state='detected', commit=commit_str)

        resp = self.client.get(
            PREFIX + '/regressions?state=active,detected')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        states = {r['state'] for r in data['items']}
        self.assertTrue(states.issubset({'active', 'detected'}))

    def test_list_filter_invalid_state_400(self):
        resp = self.client.get(PREFIX + '/regressions?state=invalid_state')
        self.assertEqual(resp.status_code, 400)

    def test_invalid_cursor_returns_400(self):
        resp = self.client.get(
            PREFIX + '/regressions?cursor=not-a-valid-cursor!!!')
        self.assertEqual(resp.status_code, 400)

    def test_list_pagination(self):
        for _ in range(3):
            _setup_regression_with_indicators(self.client, 1)
        resp = self.client.get(PREFIX + '/regressions?limit=2')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertLessEqual(len(data['items']), 2)
        if data['cursor']['next']:
            cursor = data['cursor']['next']
            resp2 = self.client.get(
                PREFIX + f'/regressions?limit=2&cursor={cursor}')
            self.assertEqual(resp2.status_code, 200)


# ==========================================================================
# Regression List Filter Tests
# ==========================================================================

class TestRegressionListFilters(unittest.TestCase):
    """Tests for test, metric, commit, and param.* filters."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def _collect_filtered(self, query_string):
        url = PREFIX + '/regressions?' + query_string + '&limit=2'
        items = collect_all_pages(self, self.client, url, page_limit=100)
        return [r['uuid'] for r in items]

    def test_list_filter_by_test(self):
        tag = uuid.uuid4().hex[:8]
        test_name = f'filter/testname/{tag}'
        commit_str = f'filter-tr1-{tag}'

        run_data = submit_run(self.client, commit_str,
                              [{'name': test_name, 'execution_time': [1.0]}])
        run_uuid = run_data['run_uuid']

        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test_name,
                         'metric': 'execution_time'}],
            commit=commit_str)

        uuids = self._collect_filtered(f'test={test_name}')
        self.assertIn(reg['uuid'], uuids)

    def test_list_filter_by_metric(self):
        tag = uuid.uuid4().hex[:8]
        test_ct = f'filter/compile/{tag}'
        test_et = f'filter/exec/{tag}'
        commit_str = f'filter-mr1-{tag}'

        run_data = submit_run(self.client, commit_str, [
            {'name': test_ct, 'compile_time': [5.0]},
            {'name': test_et, 'execution_time': [1.0]},
        ])
        run_uuid = run_data['run_uuid']

        reg_ct = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test_ct,
                         'metric': 'compile_time'}],
            commit=commit_str)

        reg_et = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test_et,
                         'metric': 'execution_time'}],
            commit=commit_str)

        uuids = self._collect_filtered('metric=execution_time')
        self.assertIn(reg_et['uuid'], uuids)
        self.assertNotIn(reg_ct['uuid'], uuids)

    def test_list_filter_by_metric_unknown_returns_400(self):
        resp = self.client.get(
            PREFIX + '/regressions?metric=nonexistent_metric')
        self.assertEqual(resp.status_code, 400)

    def test_list_filter_nonexistent_test_404(self):
        resp = self.client.get(
            PREFIX + '/regressions?test=no/such/test/xyz')
        self.assertEqual(resp.status_code, 404)

    def test_list_filter_by_commit(self):
        tag = uuid.uuid4().hex[:8]
        test = f'fc/test/{tag}'
        rev1 = f'fc-rev1-{tag}'
        rev2 = f'fc-rev2-{tag}'

        run_data1 = submit_run(self.client, rev1,
                               [{'name': test, 'execution_time': [1.0]}])
        run_data2 = submit_run(self.client, rev2,
                               [{'name': test, 'execution_time': [2.0]}])

        reg1 = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_data1['run_uuid'], 'test': test,
                         'metric': 'execution_time'}],
            commit=rev1)
        reg2 = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_data2['run_uuid'], 'test': test,
                         'metric': 'execution_time'}],
            commit=rev2)

        uuids = self._collect_filtered(f'commit={rev1}')
        self.assertIn(reg1['uuid'], uuids)
        self.assertNotIn(reg2['uuid'], uuids)

    def test_list_item_commit_value(self):
        tag = uuid.uuid4().hex[:8]
        test = f'lcv/test/{tag}'
        rev = f'lcv-rev-{tag}'

        run_data = submit_run(self.client, rev,
                              [{'name': test, 'execution_time': [1.0]}])

        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_data['run_uuid'], 'test': test,
                         'metric': 'execution_time'}],
            commit=rev)

        resp = self.client.get(PREFIX + '/regressions?limit=500')
        data = resp.get_json()
        item = _find_in_list(data['items'], reg['uuid'])
        self.assertIsNotNone(item)
        self.assertEqual(item['commit'], rev)

    def test_list_filter_by_param(self):
        """Filter by param.X=Y through indicator -> run -> run_parameters."""
        tag = uuid.uuid4().hex[:8]
        test = f'param-filter/test/{tag}'
        commit_str = f'param-filter-rev-{tag}'

        run_data = submit_run(
            self.client, commit_str,
            [{'name': test, 'execution_time': [1.0]}],
            run_parameters={'compiler': f'clang-{tag}'})
        run_uuid = run_data['run_uuid']

        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test,
                         'metric': 'execution_time'}],
            commit=commit_str)

        uuids = self._collect_filtered(f'param.compiler=clang-{tag}')
        self.assertIn(reg['uuid'], uuids)

        # Non-matching param should not include this regression
        uuids2 = self._collect_filtered('param.compiler=gcc-nonexistent')
        self.assertNotIn(reg['uuid'], uuids2)


# ==========================================================================
# Regression Create Tests
# ==========================================================================

class TestRegressionCreate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def _setup_run_and_test(self):
        """Create a run (which creates commit and tests), return (run_uuid, test_name, commit)."""
        tag = uuid.uuid4().hex[:8]
        test = f'cr/test/{tag}'
        commit_str = f'cr-rev-{tag}'
        run_data = submit_run(self.client, commit_str,
                              [{'name': test, 'execution_time': [1.0]}])
        return run_data['run_uuid'], test, commit_str

    def test_create_regression(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn('uuid', data)
        self.assertIn('indicators', data)
        self.assertEqual(len(data['indicators']), 1)
        ind = data['indicators'][0]
        self.assertIn('uuid', ind)
        self.assertEqual(ind['run_uuid'], run_uuid)
        self.assertEqual(ind['test'], test)
        self.assertEqual(ind['metric'], 'execution_time')

    def test_create_commit_required(self):
        """Creating a regression without commit returns 422."""
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_with_custom_title(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'execution_time'}
                ],
                'title': 'Custom Title',
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['title'], 'Custom Title')

    def test_create_with_state(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'execution_time'}
                ],
                'state': 'active',
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['state'], 'active')

    def test_create_default_state_detected(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['state'], 'detected')

    def test_create_with_commit(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['commit'], commit_str)

    def test_create_with_notes(self):
        _, _, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={'commit': commit_str,
                  'notes': 'Investigation notes here'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data['notes'], 'Investigation notes here')

    def test_create_nonexistent_run_404(self):
        """Indicator referencing a nonexistent run returns 404."""
        _, _, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': 'nonexistent-run-uuid',
                     'test': 'some/test', 'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_create_nonexistent_test_404(self):
        run_uuid, _, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid,
                     'test': 'nonexistent/test/xyz',
                     'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_create_unknown_metric_400(self):
        run_uuid, test, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_str,
                'indicators': [
                    {'run_uuid': run_uuid, 'test': test,
                     'metric': 'nonexistent_metric'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_nonexistent_commit_404(self):
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={'commit': 'nonexistent-commit-xyz'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_create_invalid_state_422(self):
        _, _, commit_str = self._setup_run_and_test()
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={'commit': commit_str, 'state': 'bogus_state'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_no_auth_401(self):
        resp = self.client.post(
            PREFIX + '/regressions',
            json={'commit': 'any'},
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_submit_scope_403(self):
        headers = make_scoped_headers(self.app, 'submit')
        resp = self.client.post(
            PREFIX + '/regressions',
            json={'commit': 'any'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_indicator_commit_mismatch_409(self):
        """Indicator with run from a different commit returns 409."""
        tag = uuid.uuid4().hex[:8]
        test = f'mismatch/test/{tag}'
        commit_a = f'mismatch-a-{tag}'
        commit_b = f'mismatch-b-{tag}'

        run_a = submit_run(self.client, commit_a,
                           [{'name': test, 'execution_time': [1.0]}])
        submit_run(self.client, commit_b,
                   [{'name': test, 'execution_time': [2.0]}])

        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + '/regressions',
            json={
                'commit': commit_b,
                'indicators': [
                    {'run_uuid': run_a['run_uuid'], 'test': test,
                     'metric': 'execution_time'}
                ],
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 409)


# ==========================================================================
# Regression Detail Tests
# ==========================================================================

class TestRegressionDetail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_get_detail(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['uuid'], reg_uuid)
        self.assertIn('title', data)
        self.assertIn('bug', data)
        self.assertIn('notes', data)
        self.assertIn('state', data)
        self.assertIn('commit', data)
        self.assertIn('indicators', data)
        self.assertEqual(len(data['indicators']), 1)
        ind = data['indicators'][0]
        self.assertIn('uuid', ind)
        self.assertIn('test', ind)
        self.assertIn('run_uuid', ind)
        self.assertIn('metric', ind)
        # No machine in indicators
        self.assertNotIn('machine', ind)

    def test_detail_nonexistent_404(self):
        resp = self.client.get(
            PREFIX + '/regressions/nonexistent-uuid-12345')
        self.assertEqual(resp.status_code, 404)

    def test_detail_state_is_string(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        data = resp.get_json()
        self.assertIsInstance(data['state'], str)
        self.assertEqual(data['state'], 'active')


class TestRegressionDetailETag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_etag_present_on_detail(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        self.assertEqual(resp.status_code, 200)
        etag = resp.headers.get('ETag')
        self.assertIsNotNone(etag)
        self.assertTrue(etag.startswith('W/"'))

    def test_etag_304_on_match(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        etag = resp.headers.get('ETag')

        resp2 = self.client.get(
            PREFIX + f'/regressions/{reg_uuid}',
            headers={'If-None-Match': etag},
        )
        self.assertEqual(resp2.status_code, 304)


# ==========================================================================
# Regression Update Tests
# ==========================================================================

class TestRegressionUpdate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_update_title(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'title': 'Updated Title'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['title'], 'Updated Title')

    def test_update_state(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'state': 'fixed'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['state'], 'fixed')

    def test_update_commit(self):
        """PATCH commit to a new commit works when no indicators conflict."""
        tag = uuid.uuid4().hex[:8]
        test = f'uc/test/{tag}'
        rev1 = f'uc-rev1-{tag}'
        rev2 = f'uc-rev2-{tag}'
        submit_run(self.client, rev1,
                   [{'name': test, 'execution_time': [1.0]}])
        submit_run(self.client, rev2,
                   [{'name': test, 'execution_time': [1.0]}])

        # Create regression at rev1 with no indicators
        headers = _triage_headers(self.app)
        reg = submit_regression(self.client, commit=rev1)

        # Patch to rev2 (no indicators -> no conflict)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg["uuid"]}',
            json={'commit': rev2},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['commit'], rev2)

    def test_update_commit_revalidates_indicators(self):
        """PATCH commit fails with 409 when indicators conflict."""
        tag = uuid.uuid4().hex[:8]
        test = f'ucr/test/{tag}'
        rev1 = f'ucr-rev1-{tag}'
        rev2 = f'ucr-rev2-{tag}'

        run_data1 = submit_run(self.client, rev1,
                               [{'name': test, 'execution_time': [1.0]}])
        submit_run(self.client, rev2,
                   [{'name': test, 'execution_time': [1.0]}])

        # Create regression at rev1 with an indicator for that run
        headers = _triage_headers(self.app)
        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_data1['run_uuid'], 'test': test,
                         'metric': 'execution_time'}],
            commit=rev1)

        # Attempt to change commit to rev2 -- indicator's run is at rev1
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg["uuid"]}',
            json={'commit': rev2},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 409)

    def test_update_notes(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'notes': 'Updated investigation notes'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['notes'], 'Updated investigation notes')

    def test_clear_notes(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'notes': 'Some notes'},
            headers=headers,
        )
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'notes': None},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsNone(data['notes'])

    def test_update_nonexistent_404(self):
        headers = _triage_headers(self.app)
        resp = self.client.patch(
            PREFIX + '/regressions/nonexistent-uuid',
            json={'title': 'x'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_update_no_auth_401(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'title': 'x'},
        )
        self.assertEqual(resp.status_code, 401)

    def test_update_returns_indicators(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 2)
        headers = _triage_headers(self.app)
        resp = self.client.patch(
            PREFIX + f'/regressions/{reg_uuid}',
            json={'title': 'With Indicators'},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('indicators', data)
        self.assertEqual(len(data['indicators']), 2)


# ==========================================================================
# Regression Delete Tests
# ==========================================================================

class TestRegressionDelete(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_delete_regression(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.delete(
            PREFIX + f'/regressions/{reg_uuid}',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 204)

        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_404(self):
        headers = _triage_headers(self.app)
        resp = self.client.delete(
            PREFIX + '/regressions/nonexistent-uuid',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_no_auth_401(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.delete(
            PREFIX + f'/regressions/{reg_uuid}',
        )
        self.assertEqual(resp.status_code, 401)


# ==========================================================================
# Regression Indicators Tests
# ==========================================================================

class TestRegressionIndicators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_add_indicator(self):
        """Add an indicator to an existing regression."""
        tag = uuid.uuid4().hex[:8]
        test1 = f'add/test1/{tag}'
        test2 = f'add/test2/{tag}'
        commit_str = f'add-rev-{tag}'

        run_data = submit_run(self.client, commit_str,
                              [{'name': test1, 'execution_time': [1.0]},
                               {'name': test2, 'execution_time': [2.0]}])
        run_uuid = run_data['run_uuid']

        # Create regression with one indicator
        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test1,
                         'metric': 'execution_time'}],
            commit=commit_str)

        # Add another indicator for the same run
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg["uuid"]}/indicators',
            json={'indicators': [
                {'run_uuid': run_uuid, 'test': test2,
                 'metric': 'execution_time'}
            ]},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['indicators']), 2)

    def test_add_duplicate_silently_ignored(self):
        tag = uuid.uuid4().hex[:8]
        test = f'dup/test/{tag}'
        commit_str = f'dup-rev-{tag}'

        run_data = submit_run(self.client, commit_str,
                              [{'name': test, 'execution_time': [1.0]}])
        run_uuid = run_data['run_uuid']

        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_uuid, 'test': test,
                         'metric': 'execution_time'}],
            commit=commit_str)

        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg["uuid"]}/indicators',
            json={'indicators': [
                {'run_uuid': run_uuid, 'test': test,
                 'metric': 'execution_time'}
            ]},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['indicators']), 1)

    def test_add_indicator_wrong_commit_409(self):
        """Adding an indicator whose run is at a different commit returns 409."""
        tag = uuid.uuid4().hex[:8]
        test = f'wrong-commit/test/{tag}'
        commit_a = f'wc-rev-a-{tag}'
        commit_b = f'wc-rev-b-{tag}'

        run_a = submit_run(self.client, commit_a,
                           [{'name': test, 'execution_time': [1.0]}])
        run_b = submit_run(self.client, commit_b,
                           [{'name': test, 'execution_time': [2.0]}])

        # Create regression at commit_a
        reg = submit_regression(
            self.client,
            indicators=[{'run_uuid': run_a['run_uuid'], 'test': test,
                         'metric': 'execution_time'}],
            commit=commit_a)

        # Try to add indicator from commit_b's run
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg["uuid"]}/indicators',
            json={'indicators': [
                {'run_uuid': run_b['run_uuid'], 'test': test,
                 'metric': 'execution_time'}
            ]},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 409)

    def test_add_nonexistent_run_404(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicators': [
                {'run_uuid': 'nonexistent-run-uuid',
                 'test': 'some/test', 'metric': 'execution_time'}
            ]},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

    def test_add_unknown_metric_400(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(PREFIX + f'/regressions/{reg_uuid}')
        existing = resp.get_json()['indicators'][0]

        headers = _triage_headers(self.app)
        resp2 = self.client.post(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicators': [
                {'run_uuid': existing['run_uuid'],
                 'test': existing['test'],
                 'metric': 'nonexistent_metric'}
            ]},
            headers=headers,
        )
        self.assertEqual(resp2.status_code, 400)

    def test_add_indicator_no_auth_401(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicators': [
                {'run_uuid': 'x', 'test': 'y', 'metric': 'z'}
            ]},
        )
        self.assertEqual(resp.status_code, 401)

    def test_add_empty_list_422(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.post(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicators': []},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_remove_indicator(self):
        reg_uuid, ind_uuids = _setup_regression_with_indicators(
            self.client, 2)
        headers = _triage_headers(self.app)
        resp = self.client.delete(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicator_uuids': [ind_uuids[0]]},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['indicators']), 1)

    def test_remove_unknown_uuid_silently_ignored(self):
        reg_uuid, ind_uuids = _setup_regression_with_indicators(
            self.client, 1)
        headers = _triage_headers(self.app)
        resp = self.client.delete(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicator_uuids': ['nonexistent-uuid-xyz']},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['indicators']), 1)

    def test_remove_no_auth_401(self):
        reg_uuid, ind_uuids = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.delete(
            PREFIX + f'/regressions/{reg_uuid}/indicators',
            json={'indicator_uuids': [ind_uuids[0]]},
        )
        self.assertEqual(resp.status_code, 401)


class TestRegressionZPagination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._reg_uuids = []
        for _ in range(5):
            reg_uuid, _ = _setup_regression_with_indicators(
                cls.client, 1)
            cls._reg_uuids.append(reg_uuid)

    def _collect_all_pages(self):
        url = PREFIX + '/regressions?limit=2'
        return collect_all_pages(self, self.client, url, page_limit=100)

    def test_pagination_collects_all_items(self):
        all_items = self._collect_all_pages()
        collected_uuids = [item['uuid'] for item in all_items]
        for reg_uuid in self._reg_uuids:
            self.assertIn(reg_uuid, collected_uuids)

    def test_no_duplicate_items_across_pages(self):
        all_items = self._collect_all_pages()
        uuids = [item['uuid'] for item in all_items]
        self.assertEqual(len(uuids), len(set(uuids)))


class TestRegressionUnknownParams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_regressions_list_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/regressions?bogus=1')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('bogus', data['error']['message'])

    def test_regression_detail_unknown_param_returns_400(self):
        reg_uuid, _ = _setup_regression_with_indicators(
            self.client, 1)
        resp = self.client.get(
            PREFIX + f'/regressions/{reg_uuid}?bogus=1')
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
