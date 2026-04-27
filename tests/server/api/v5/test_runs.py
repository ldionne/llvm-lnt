# Tests for the v5 run endpoints.
#
# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     %{utils}/with_temporary_instance.py --db-version 5.0 %t.instance \
# RUN:         -- python %s %t.instance
# END.

import datetime
import json
import sys
import os
import unittest
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from v5_test_helpers import (
    create_app, create_client, admin_headers, make_scoped_headers,
    create_commit, create_run,
    collect_all_pages, submit_run,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


def _make_submission_payload(commit_str=None, run_parameters=None):
    """Build a valid v5-format JSON submission payload."""
    if commit_str is None:
        commit_str = f'r{uuid.uuid4().hex[:8]}'

    payload = {
        'format_version': '5',
        'commit': commit_str,
        'tests': [
            {
                'name': 'test.suite/benchmark1',
                'execution_time': 0.1234,
            },
        ],
    }
    if run_parameters:
        payload['run_parameters'] = run_parameters
    return json.dumps(payload)


class TestRunListEmpty(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/runs with no data."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_returns_200(self):
        resp = self.client.get(PREFIX + '/runs')
        self.assertEqual(resp.status_code, 200)

    def test_list_has_items_key(self):
        resp = self.client.get(PREFIX + '/runs')
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertIsInstance(data['items'], list)

    def test_list_has_pagination_envelope(self):
        resp = self.client.get(PREFIX + '/runs')
        data = resp.get_json()
        self.assertIn('cursor', data)
        self.assertIn('next', data['cursor'])
        self.assertIn('previous', data['cursor'])


class TestRunListWithData(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/runs with existing data."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_includes_created_runs(self):
        """Runs created via API appear in list."""
        rev = f'list-rev-{uuid.uuid4().hex[:6]}'
        params = {'os': f'linux-{uuid.uuid4().hex[:6]}'}
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}],
                          run_parameters=params)
        run_uuid = data['run_uuid']

        resp = self.client.get(
            PREFIX + f'/runs?param.os={params["os"]}')
        rdata = resp.get_json()
        uuids = [item['uuid'] for item in rdata['items']]
        self.assertIn(run_uuid, uuids)

    def test_list_run_has_expected_fields(self):
        """Each run in the list has uuid, commit, submitted_at, run_parameters."""
        rev = f'fields-rev-{uuid.uuid4().hex[:6]}'
        params = {'marker': f'fields-{uuid.uuid4().hex[:6]}'}
        submit_run(self.client, rev,
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters=params)

        resp = self.client.get(
            PREFIX + f'/runs?param.marker={params["marker"]}')
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        item = data['items'][0]
        self.assertIn('uuid', item)
        self.assertIn('commit', item)
        self.assertIn('submitted_at', item)
        self.assertIn('run_parameters', item)
        # Must NOT have internal IDs, machine, or v4 fields
        self.assertNotIn('id', item)
        self.assertNotIn('machine', item)
        self.assertNotIn('machine_id', item)
        self.assertNotIn('order', item)

    def test_list_never_exposes_internal_ids(self):
        """Run list items never contain internal database IDs."""
        rev = f'noid-rev-{uuid.uuid4().hex[:6]}'
        params = {'marker': f'noid-{uuid.uuid4().hex[:6]}'}
        submit_run(self.client, rev,
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters=params)

        resp = self.client.get(
            PREFIX + f'/runs?param.marker={params["marker"]}')
        data = resp.get_json()
        for item in data['items']:
            self.assertNotIn('id', item)
            self.assertNotIn('machine_id', item)
            self.assertNotIn('commit_id', item)


class TestRunListPagination(unittest.TestCase):
    """Tests for run list pagination."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_pagination(self):
        """Create multiple runs and paginate through them."""
        tag = uuid.uuid4().hex[:8]
        params = {'pagtest': tag}
        for i in range(3):
            submit_run(self.client,
                       f'page-rev-{uuid.uuid4().hex[:6]}-{i}',
                       [{'name': 'p/test', 'execution_time': 0.0}],
                       run_parameters=params)

        # Get first page with limit=2
        resp = self.client.get(
            PREFIX + f'/runs?param.pagtest={tag}&limit=2')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 2)
        self.assertIsNotNone(data['cursor']['next'])

        # Follow cursor
        cursor = data['cursor']['next']
        resp2 = self.client.get(
            PREFIX + f'/runs?param.pagtest={tag}&limit=2&cursor={cursor}')
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertEqual(len(data2['items']), 1)
        self.assertIsNone(data2['cursor']['next'])


class TestRunSubmit(unittest.TestCase):
    """Tests for POST /api/v5/{ts}/runs (run submission)."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_submit_valid_payload(self):
        """Submit a valid JSON payload and verify response."""
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('run_uuid', data)
        self.assertIsNotNone(data['run_uuid'])
        self.assertIn('result_url', data)

    def test_submit_returns_uuid(self):
        """Submitted run has a valid UUID."""
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        data = resp.get_json()
        run_uuid = data.get('run_uuid')
        self.assertIsNotNone(run_uuid)
        try:
            uuid.UUID(run_uuid, version=4)
        except ValueError:
            self.fail(f"run_uuid is not a valid UUID: {run_uuid}")

    def test_submit_run_detail_accessible(self):
        """After submission, the run detail is accessible by UUID."""
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        data = resp.get_json()
        run_uuid = data['run_uuid']

        detail_resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(detail_resp.status_code, 200)
        detail = detail_resp.get_json()
        self.assertEqual(detail['uuid'], run_uuid)

    def test_submit_invalid_payload_422(self):
        """Submitting a JSON object without required fields returns 422."""
        resp = self.client.post(
            PREFIX + '/runs',
            data='{"not": "valid report"}',
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_submit_empty_body_422(self):
        """Submitting an empty body returns 422."""
        resp = self.client.post(
            PREFIX + '/runs',
            data='',
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_submit_no_auth_401(self):
        """Submitting without auth returns 401."""
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_submit_read_scope_403(self):
        """Submitting with read scope returns 403."""
        headers = make_scoped_headers(self.app, 'read')
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_submit_with_submit_scope_succeeds(self):
        """Submitting with submit scope succeeds."""
        headers = make_scoped_headers(self.app, 'submit')
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)

    def test_submit_result_url_format(self):
        """Result URL should point to the v5 run detail."""
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        data = resp.get_json()
        result_url = data.get('result_url')
        self.assertIsNotNone(result_url)
        self.assertIn(f'/api/v5/{TS}/runs/', result_url)


class TestRunSubmitFormatValidation(unittest.TestCase):
    """Tests that POST /api/v5/{ts}/runs mandates format_version '5'."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_submit_non_json_body_400(self):
        resp = self.client.post(
            PREFIX + '/runs',
            data='not json at all',
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_submit_json_array_body_422(self):
        resp = self.client.post(
            PREFIX + '/runs',
            data='[1, 2, 3]',
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_submit_missing_format_version_422(self):
        payload = json.dumps({
            'commit': 'rev1',
            'tests': [],
        })
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_submit_wrong_format_version_400(self):
        payload = json.dumps({
            'format_version': '2',
            'commit': 'rev1',
            'tests': [],
        })
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 400)
        msg = resp.get_json()['error']['message']
        self.assertIn('format_version', msg)

    def test_submit_integer_format_version_400(self):
        payload = json.dumps({
            'format_version': 5,
            'commit': 'rev1',
            'tests': [],
        })
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 400)
        msg = resp.get_json()['error']['message']
        self.assertIn('format_version', msg)

    def test_submit_v5_format_accepted(self):
        payload = _make_submission_payload()
        resp = self.client.post(
            PREFIX + '/runs',
            data=payload,
            content_type='application/json',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.get_json().get('success'))


class TestRunDetail(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/runs/{uuid}."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_get_run_detail(self):
        rev = f'detail-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['uuid'], run_uuid)
        self.assertIn('commit', data)
        self.assertIn('submitted_at', data)
        self.assertIn('run_parameters', data)
        self.assertNotIn('machine', data)

    def test_get_run_detail_has_no_internal_ids(self):
        rev = f'noid-detail-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        data = resp.get_json()
        self.assertNotIn('id', data)
        self.assertNotIn('machine_id', data)
        self.assertNotIn('commit_id', data)

    def test_get_nonexistent_uuid_404(self):
        fake_uuid = str(uuid.uuid4())
        resp = self.client.get(PREFIX + f'/runs/{fake_uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_get_invalid_uuid_format_404(self):
        resp = self.client.get(PREFIX + '/runs/not-a-valid-uuid')
        self.assertEqual(resp.status_code, 404)


class TestRunDetailETag(unittest.TestCase):
    """ETag tests for GET /api/v5/{ts}/runs/{uuid}."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_etag_present_on_detail(self):
        rev = f'etag-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(resp.status_code, 200)
        etag = resp.headers.get('ETag')
        self.assertIsNotNone(etag)
        self.assertTrue(etag.startswith('W/"'))

    def test_etag_304_on_match(self):
        rev = f'etag-304-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        etag = resp.headers.get('ETag')

        resp2 = self.client.get(
            PREFIX + f'/runs/{run_uuid}',
            headers={'If-None-Match': etag},
        )
        self.assertEqual(resp2.status_code, 304)

    def test_etag_200_on_mismatch(self):
        rev = f'etag-200-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.get(
            PREFIX + f'/runs/{run_uuid}',
            headers={'If-None-Match': 'W/"stale-etag-value"'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.get_json())


class TestRunDelete(unittest.TestCase):
    """Tests for DELETE /api/v5/{ts}/runs/{uuid}."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_delete_run(self):
        rev = f'del-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.delete(
            PREFIX + f'/runs/{run_uuid}',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 204)

        resp = self.client.get(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_404(self):
        fake_uuid = str(uuid.uuid4())
        resp = self.client.delete(
            PREFIX + f'/runs/{fake_uuid}',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_no_auth_401(self):
        rev = f'del-noauth-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        resp = self.client.delete(PREFIX + f'/runs/{run_uuid}')
        self.assertEqual(resp.status_code, 401)

    def test_delete_triage_scope_403(self):
        rev = f'del-scope-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        headers = make_scoped_headers(self.app, 'triage')
        resp = self.client.delete(
            PREFIX + f'/runs/{run_uuid}',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_manage_scope_204(self):
        rev = f'del-mng-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']

        headers = make_scoped_headers(self.app, 'manage')
        resp = self.client.delete(
            PREFIX + f'/runs/{run_uuid}',
            headers=headers,
        )
        self.assertEqual(resp.status_code, 204)

    def test_delete_with_indicators_409(self):
        """Deleting a run referenced by regression indicators returns 409."""
        from v5_test_helpers import create_commit, create_run, create_test, create_regression
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        c = create_commit(session, ts, commit=f'del-ind-{uuid.uuid4().hex[:8]}')
        run = create_run(session, ts, c)
        run_uuid = run.uuid
        t = create_test(session, ts, name=f'del-ind/test/{uuid.uuid4().hex[:8]}')

        create_regression(
            session, ts,
            indicators=[{'run_id': run.id, 'test_id': t.id,
                         'metric': 'execution_time'}],
            commit=c)
        session.commit()
        session.close()

        resp = self.client.delete(
            PREFIX + f'/runs/{run_uuid}',
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 409)


class TestRunFilterByParam(unittest.TestCase):
    """Test filtering runs by param.* query parameters."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_filter_by_single_param(self):
        """Filter runs by a single param.X=Y."""
        tag = uuid.uuid4().hex[:8]
        params = {'os': f'linux-{tag}'}
        rev = f'param-rev-{uuid.uuid4().hex[:6]}'
        submit_run(self.client, rev,
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters=params)

        resp = self.client.get(PREFIX + f'/runs?param.os={params["os"]}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)

    def test_filter_by_nonexistent_param(self):
        """Filtering by a param value that doesn't exist returns empty."""
        resp = self.client.get(
            PREFIX + '/runs?param.os=nonexistent-os-xyz-abc')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)

    def test_filter_and_combination(self):
        """Different param keys combine with AND."""
        tag = uuid.uuid4().hex[:8]
        params_both = {'os': f'linux-{tag}', 'arch': f'x86-{tag}'}
        params_os_only = {'os': f'linux-{tag}', 'arch': f'arm-{tag}'}

        submit_run(self.client, f'and-rev1-{uuid.uuid4().hex[:6]}',
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters=params_both)
        submit_run(self.client, f'and-rev2-{uuid.uuid4().hex[:6]}',
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters=params_os_only)

        # Filter by both os AND arch=x86
        resp = self.client.get(
            PREFIX + f'/runs?param.os=linux-{tag}&param.arch=x86-{tag}')
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)

    def test_filter_or_combination(self):
        """Multiple values for same key combine with OR."""
        tag = uuid.uuid4().hex[:8]
        submit_run(self.client, f'or-rev1-{uuid.uuid4().hex[:6]}',
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters={'os': f'linux-{tag}'})
        submit_run(self.client, f'or-rev2-{uuid.uuid4().hex[:6]}',
                   [{'name': 'p/test', 'execution_time': 0.0}],
                   run_parameters={'os': f'macos-{tag}'})

        resp = self.client.get(
            PREFIX + f'/runs?param.os=linux-{tag}&param.os=macos-{tag}')
        data = resp.get_json()
        self.assertEqual(len(data['items']), 2)


class TestRunFilterByCommit(unittest.TestCase):
    """Test filtering runs by commit string."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_filter_by_commit(self):
        rev1 = f'cfilt-rev1-{uuid.uuid4().hex[:6]}'
        rev2 = f'cfilt-rev2-{uuid.uuid4().hex[:6]}'
        submit_run(self.client, rev1,
                   [{'name': 'p/test', 'execution_time': 0.0}])
        submit_run(self.client, rev2,
                   [{'name': 'p/test', 'execution_time': 0.0}])

        resp = self.client.get(PREFIX + f'/runs?commit={rev1}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['commit'], rev1)

    def test_filter_by_nonexistent_commit(self):
        resp = self.client.get(
            PREFIX + '/runs?commit=nonexistent-commit-xyz-abc')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)


class TestRunFilterByDatetime(unittest.TestCase):
    """Test filtering runs by after/before datetime."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_filter_after(self):
        tag = uuid.uuid4().hex[:8]
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        c1 = create_commit(session, ts, commit=f'after-rev1-{tag}')
        create_run(session, ts, c1,
                   submitted_at=datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
                   run_parameters={'dtmarker': tag})
        c2 = create_commit(session, ts, commit=f'after-rev2-{tag}')
        create_run(session, ts, c2,
                   submitted_at=datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
                   run_parameters={'dtmarker': tag})
        session.commit()
        session.close()

        resp = self.client.get(
            PREFIX + f'/runs?param.dtmarker={tag}&after=2024-03-01T00:00:00')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)

    def test_filter_invalid_after_datetime_400(self):
        resp = self.client.get(PREFIX + '/runs?after=not-a-date')
        self.assertEqual(resp.status_code, 400)

    def test_filter_invalid_before_datetime_400(self):
        resp = self.client.get(PREFIX + '/runs?before=not-a-date')
        self.assertEqual(resp.status_code, 400)


class TestRunSort(unittest.TestCase):
    """Test sorting runs."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_sort_descending_submitted_at(self):
        tag = uuid.uuid4().hex[:8]
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        for month in (1, 4, 7):
            c = create_commit(session, ts,
                              commit=f'sort-rev-{month}-{tag}')
            create_run(session, ts, c,
                       submitted_at=datetime.datetime(
                           2024, month, 1, 12, 0, 0,
                           tzinfo=datetime.timezone.utc),
                       run_parameters={'sortmarker': tag})
        session.commit()
        session.close()

        resp_default = self.client.get(
            PREFIX + f'/runs?param.sortmarker={tag}')
        self.assertEqual(resp_default.status_code, 200)
        default_times = [
            item['submitted_at']
            for item in resp_default.get_json()['items']]

        resp_sorted = self.client.get(
            PREFIX + f'/runs?param.sortmarker={tag}&sort=-submitted_at')
        self.assertEqual(resp_sorted.status_code, 200)
        sorted_times = [
            item['submitted_at']
            for item in resp_sorted.get_json()['items']]

        self.assertEqual(len(sorted_times), 3)
        self.assertEqual(sorted_times, list(reversed(default_times)))


class TestRunPagination(unittest.TestCase):
    """Exhaustive cursor pagination tests for GET /api/v5/{ts}/runs."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._tag = uuid.uuid4().hex[:8]
        for i in range(5):
            submit_run(cls.client,
                       f'pag-run-rev-{uuid.uuid4().hex[:6]}-{i}',
                       [{'name': 'p/test', 'execution_time': 0.0}],
                       run_parameters={'pagtag': cls._tag})

    def _collect_all_pages(self):
        url = PREFIX + f'/runs?param.pagtag={self._tag}&limit=2'
        return collect_all_pages(self, self.client, url)

    def test_pagination_collects_all_items(self):
        all_items = self._collect_all_pages()
        self.assertEqual(len(all_items), 5)

    def test_no_duplicate_items_across_pages(self):
        all_items = self._collect_all_pages()
        uuids = [item['uuid'] for item in all_items]
        self.assertEqual(len(uuids), len(set(uuids)))


class TestRunListInvalidCursor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_invalid_cursor_returns_400(self):
        resp = self.client.get(
            PREFIX + '/runs?cursor=not-a-valid-cursor!!!')
        self.assertEqual(resp.status_code, 400)


class TestRunUnknownParams(unittest.TestCase):
    """Test that unknown query parameters are rejected with 400."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_runs_list_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/runs?bogus=1')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('bogus', data['error']['message'])

    def test_run_detail_unknown_param_returns_400(self):
        rev = f'unk-det-rev-{uuid.uuid4().hex[:6]}'
        data = submit_run(self.client, rev,
                          [{'name': 'p/test', 'execution_time': 0.0}])
        run_uuid = data['run_uuid']
        resp = self.client.get(PREFIX + f'/runs/{run_uuid}?bogus=1')
        self.assertEqual(resp.status_code, 400)

    def test_param_prefix_accepted(self):
        """param.* query parameters are accepted (not rejected as unknown)."""
        resp = self.client.get(PREFIX + '/runs?param.os=linux')
        self.assertEqual(resp.status_code, 200)

    def test_run_submit_unknown_param_rejected(self):
        """Unknown query params on POST /runs are rejected."""
        headers = admin_headers()
        headers['Content-Type'] = 'application/json'
        body = json.dumps({
            'format_version': '5',
            'commit': 'rev-ignore-test',
            'tests': [],
        })
        resp = self.client.post(
            PREFIX + '/runs?ignore_regressions=true',
            data=body,
            headers=headers,
        )
        self.assertIn(resp.status_code, [400, 422])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
