# End-to-end integration tests for the v5 REST API.
#
# These tests exercise multi-endpoint workflows to verify that the
# endpoints work together correctly, unlike the per-endpoint unit tests.
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
from v5_test_helpers import create_app, create_client, admin_headers, submit_run


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


_DEFAULT_TESTS = [
    {
        'name': 'test.suite/benchmark1',
        'execution_time': [0.1234, 0.1235],
    },
    {
        'name': 'test.suite/benchmark2',
        'compile_time': 13.12,
        'execution_time': 0.2135,
    },
]


# -----------------------------------------------------------------------
# 1. TestRunSubmissionWorkflow
# -----------------------------------------------------------------------

class TestRunSubmissionWorkflow(unittest.TestCase):
    """Submit a run, then verify it via multiple GET endpoints.

    This workflow exercises:
      POST   /runs              (submit)
      GET    /runs              (list)
      GET    /runs/{uuid}       (detail)
      GET    /tests             (implicit test creation)
      GET    /commits/{value}   (implicit commit creation)
    """

    app = None
    client = None
    _revision = None
    _run_uuid = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._revision = f'r{uuid.uuid4().hex[:8]}'
        data = submit_run(
            cls.client, cls._revision, _DEFAULT_TESTS,
            run_parameters={'compiler': 'clang-test', 'os': 'linux'})
        cls._run_uuid = data.get('run_uuid')

    def test_01_submission_succeeded(self):
        self.assertIsNotNone(self._run_uuid)
        uuid.UUID(self._run_uuid, version=4)

    def test_02_run_appears_in_list(self):
        resp = self.client.get(
            PREFIX + f'/runs?param.compiler=clang-test')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        uuids = [item['uuid'] for item in data['items']]
        self.assertIn(self._run_uuid, uuids)

    def test_03_run_detail_is_correct(self):
        resp = self.client.get(PREFIX + f'/runs/{self._run_uuid}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['uuid'], self._run_uuid)
        self.assertIn('commit', data)
        self.assertEqual(data['commit'], self._revision)
        self.assertIn('submitted_at', data)
        self.assertIn('run_parameters', data)
        self.assertEqual(data['run_parameters']['compiler'], 'clang-test')
        # No machine in v5
        self.assertNotIn('machine', data)

    def test_04_tests_created_implicitly(self):
        resp = self.client.get(
            PREFIX + '/tests?search=test.suite/benchmark1')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        names = [t['name'] for t in data['items']]
        has_benchmark1 = any('benchmark1' in n for n in names)
        self.assertTrue(has_benchmark1)

    def test_05_commit_created_implicitly(self):
        resp = self.client.get(
            PREFIX + f'/commits/{self._revision}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['commit'], self._revision)


# -----------------------------------------------------------------------
# 2. TestAPIKeyLifecycle
# -----------------------------------------------------------------------

class TestAPIKeyLifecycle(unittest.TestCase):
    """Create an API key, use it, revoke it, verify rejection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_api_key_full_lifecycle(self):
        # Step 1: Create a submit-scoped key
        create_resp = self.client.post(
            '/api/v5/admin/api-keys',
            json={'name': 'lifecycle-key', 'scope': 'submit'},
            headers=admin_headers(),
        )
        self.assertEqual(create_resp.status_code, 201)
        created = create_resp.get_json()
        raw_token = created['key']
        prefix = created['prefix']
        key_headers = {'Authorization': f'Bearer {raw_token}'}

        # Step 2: Use the new key on a read endpoint
        read_resp = self.client.get('/api/v5/', headers=key_headers)
        self.assertEqual(read_resp.status_code, 200)

        # Step 3: Use the new key to submit a run
        commit = f'r{uuid.uuid4().hex[:8]}'
        submit_resp = self.client.post(
            PREFIX + '/runs',
            json={
                'format_version': '5',
                'commit': commit,
                'tests': _DEFAULT_TESTS,
            },
            headers=key_headers,
        )
        self.assertIn(submit_resp.status_code, [201, 301])

        # Step 4: Revoke the key
        revoke_resp = self.client.delete(
            f'/api/v5/admin/api-keys/{prefix}',
            headers=admin_headers(),
        )
        self.assertEqual(revoke_resp.status_code, 204)

        # Step 5: Verify rejection
        reject_resp = self.client.get(
            '/api/v5/admin/api-keys', headers=key_headers)
        self.assertEqual(reject_resp.status_code, 401)


# -----------------------------------------------------------------------
# 3. TestDiscoveryNavigability
# -----------------------------------------------------------------------

class TestDiscoveryNavigability(unittest.TestCase):
    """Follow every link from the discovery endpoint and verify 200."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_all_discovery_links_resolve(self):
        disco_resp = self.client.get('/api/v5/')
        self.assertEqual(disco_resp.status_code, 200)
        data = disco_resp.get_json()

        self.assertIn('test_suites', data)
        self.assertGreater(len(data['test_suites']), 0)

        for suite in data['test_suites']:
            self.assertIn('name', suite)
            self.assertIn('links', suite)
            suite_name = suite['name']

            for link_name, url in suite['links'].items():
                resp = self.client.get(url)
                self.assertEqual(
                    resp.status_code, 200,
                    f"Link '{link_name}' ({url}) for suite '{suite_name}' "
                    f"returned {resp.status_code}")

    def test_discovery_nts_suite_has_all_expected_links(self):
        disco_resp = self.client.get('/api/v5/')
        data = disco_resp.get_json()

        nts_suites = [s for s in data['test_suites'] if s['name'] == 'nts']
        self.assertEqual(len(nts_suites), 1)
        links = nts_suites[0]['links']

        expected_keys = {
            'commits', 'runs', 'tests',
            'regressions', 'run_parameters', 'dashboard',
        }
        self.assertEqual(set(links.keys()), expected_keys)


# -----------------------------------------------------------------------
# 4. TestCORSOnAllEndpoints
# -----------------------------------------------------------------------

class TestCORSOnAllEndpoints(unittest.TestCase):
    """Verify CORS headers are present on various endpoint types."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def _assert_cors(self, resp, context):
        self.assertEqual(
            resp.headers.get('Access-Control-Allow-Origin'), '*',
            f"Missing CORS header on {context} (status {resp.status_code})")

    def test_cors_on_discovery(self):
        resp = self.client.get('/api/v5/')
        self._assert_cors(resp, 'GET /api/v5/')

    def test_cors_on_run_list(self):
        resp = self.client.get(PREFIX + '/runs')
        self._assert_cors(resp, 'GET /runs')

    def test_cors_on_run_submit(self):
        commit = f'r{uuid.uuid4().hex[:8]}'
        resp = self.client.post(
            PREFIX + '/runs',
            json={
                'format_version': '5',
                'commit': commit,
                'tests': _DEFAULT_TESTS,
            },
            headers=admin_headers(),
        )
        self._assert_cors(resp, 'POST /runs')

    def test_cors_on_options_preflight(self):
        resp = self.client.options(
            PREFIX + '/runs',
            headers={
                'Origin': 'https://example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )
        self._assert_cors(resp, 'OPTIONS /runs')
        self.assertIn(
            'GET',
            resp.headers.get('Access-Control-Allow-Methods', ''))
        self.assertIn(
            'Authorization',
            resp.headers.get('Access-Control-Allow-Headers', ''))


# -----------------------------------------------------------------------
# 5. TestSamplesQueryWorkflow
# -----------------------------------------------------------------------

class TestSamplesQueryWorkflow(unittest.TestCase):
    """Submit runs with data, then query them via POST /samples.

    This workflow exercises:
      POST   /runs              (submit with data)
      PATCH  /commits/{value}   (assign ordinals)
      POST   /samples           (unified sample query)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._test = f'samples-wf/test/{uuid.uuid4().hex[:8]}'
        cls._commits = []

        import datetime
        from v5_test_helpers import (
            create_commit, create_run,
            create_test, create_sample,
        )
        db = cls.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]
        test = create_test(session, ts, name=cls._test)
        for i in range(5):
            c = create_commit(session, ts,
                              commit=f'swf-c-{uuid.uuid4().hex[:8]}')
            ts.update_commit(session, c, ordinal=5000 + i)
            run = create_run(session, ts, c,
                             submitted_at=datetime.datetime(
                                 2024, 1, 1 + i, 12, 0, 0),
                             run_parameters={'env': 'samples-wf'})
            create_sample(session, ts, run, test,
                          execution_time=10.0 + i)
            cls._commits.append(c.commit)
        session.commit()
        session.close()

    def test_samples_query_returns_all_data(self):
        """POST /samples returns all 5 submitted data points."""
        resp = self.client.post(
            PREFIX + '/samples',
            json={
                'metric': 'execution_time',
                'params': {'env': 'samples-wf'},
                'test': [self._test],
            },
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()['items']
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertIn('commit', item)
            self.assertIn('ordinal', item)
            self.assertIn('submitted_at', item)
            self.assertIn('execution_time', item)
            self.assertNotIn('machine', item)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
