# Tests for the v5 API error format.
#
# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     %{utils}/with_temporary_instance.py --db-version 5.0 %t.instance \
# RUN:         -- python %s %t.instance
# END.

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from v5_test_helpers import create_app, create_client, admin_headers


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


class TestErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_404_has_error_key(self):
        resp = self.client.get('/api/v5/nonexistent_suite/runs')
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertIn('code', data['error'])
        self.assertIn('message', data['error'])

    def test_404_error_code(self):
        resp = self.client.get('/api/v5/nonexistent_suite/runs')
        data = resp.get_json()
        self.assertEqual(data['error']['code'], 'not_found')

    def test_405_method_not_allowed(self):
        """POST to a GET-only endpoint should return 405."""
        resp = self.client.post('/api/v5/')
        self.assertEqual(resp.status_code, 405)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], 'method_not_allowed')

    def test_v5_errors_are_json(self):
        resp = self.client.get('/api/v5/nonexistent_suite/runs')
        self.assertTrue(resp.content_type.startswith('application/json'))

    def test_v5_error_cors_headers(self):
        resp = self.client.get('/api/v5/nonexistent_suite/runs')
        self.assertEqual(
            resp.headers.get('Access-Control-Allow-Origin'), '*')


class TestV5ApiError(unittest.TestCase):
    """Test the V5ApiError exception class directly."""

    def test_v5_api_error_attributes(self):
        from lnt.server.api.v5.errors import V5ApiError
        exc = V5ApiError(404, 'not_found', 'Run not found')
        self.assertEqual(exc.status_code, 404)
        self.assertEqual(exc.error_code, 'not_found')
        self.assertEqual(exc.message, 'Run not found')
        self.assertEqual(str(exc), 'Run not found')

    def test_v5_api_error_is_exception(self):
        from lnt.server.api.v5.errors import V5ApiError
        exc = V5ApiError(400, 'validation_error', 'Bad input')
        self.assertIsInstance(exc, Exception)

    def test_abort_with_error_raises_v5_api_error(self):
        from lnt.server.api.v5.errors import V5ApiError, abort_with_error
        with self.assertRaises(V5ApiError) as ctx:
            abort_with_error(400, 'test message')
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.error_code, 'validation_error')
        self.assertEqual(ctx.exception.message, 'test message')

    def test_abort_with_error_unknown_status(self):
        from lnt.server.api.v5.errors import V5ApiError, abort_with_error
        with self.assertRaises(V5ApiError) as ctx:
            abort_with_error(418, "I'm a teapot")
        self.assertEqual(ctx.exception.status_code, 418)
        self.assertEqual(ctx.exception.error_code, 'error')


class TestAbortWithErrorIntegration(unittest.TestCase):
    """Test that abort_with_error produces correct HTTP responses end-to-end."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls.headers = admin_headers()

    def test_400_from_abort_with_error(self):
        """POST /runs with invalid body triggers abort_with_error(400, ...)."""
        resp = self.client.post(
            PREFIX + '/runs',
            data='not json',
            content_type='application/json',
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], 'validation_error')
        self.assertIsInstance(data['error']['message'], str)
        self.assertTrue(resp.content_type.startswith('application/json'))

    def test_404_from_abort_with_error(self):
        """GET a non-existent run triggers abort_with_error(404, ...)."""
        fake_uuid = '00000000-0000-0000-0000-000000000000'
        resp = self.client.get(
            PREFIX + f'/runs/{fake_uuid}',
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], 'not_found')
        self.assertTrue(resp.content_type.startswith('application/json'))

    def test_409_delete_run_with_indicators(self):
        """Deleting a run referenced by regression indicators returns 409."""
        import uuid as _uuid
        from v5_test_helpers import (
            create_commit, create_run, create_test, create_regression,
        )
        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        c = create_commit(session, ts,
                          commit=f'err-409-{_uuid.uuid4().hex[:8]}')
        run = create_run(session, ts, c)
        t = create_test(session, ts,
                        name=f'err-409/test/{_uuid.uuid4().hex[:8]}')
        create_regression(
            session, ts,
            indicators=[{'run_id': run.id, 'test_id': t.id,
                         'metric': 'execution_time'}],
            commit=c)
        session.commit()
        run_uuid = run.uuid
        session.close()

        resp = self.client.delete(
            PREFIX + f'/runs/{run_uuid}',
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], 'conflict')

    def test_error_response_has_exactly_two_keys(self):
        """Error envelope should contain exactly 'code' and 'message'."""
        fake_uuid = '00000000-0000-0000-0000-000000000000'
        resp = self.client.get(
            PREFIX + f'/runs/{fake_uuid}',
            headers=self.headers,
        )
        data = resp.get_json()
        self.assertEqual(set(data['error'].keys()), {'code', 'message'})


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
