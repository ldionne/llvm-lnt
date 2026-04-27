# Tests for the v5 dashboard endpoints.
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
from v5_test_helpers import (
    create_app, create_client, admin_headers, make_scoped_headers,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


class TestDashboardGet(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/dashboard."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_get_returns_200(self):
        resp = self.client.get(PREFIX + '/dashboard')
        self.assertEqual(resp.status_code, 200)

    def test_get_has_cards_key(self):
        resp = self.client.get(PREFIX + '/dashboard')
        data = resp.get_json()
        self.assertIn('cards', data)
        self.assertIsInstance(data['cards'], list)

    def test_get_empty_initially(self):
        resp = self.client.get(PREFIX + '/dashboard')
        data = resp.get_json()
        self.assertEqual(len(data['cards']), 0)

    def test_get_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/dashboard?bogus=1')
        self.assertEqual(resp.status_code, 400)


class TestDashboardPut(unittest.TestCase):
    """Tests for PUT /api/v5/{ts}/dashboard."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._manage_headers = make_scoped_headers(cls.app, 'manage')

    def test_put_replaces_cards(self):
        cards = [
            {'position': 0, 'params': {'compiler': 'clang'},
             'metric': 'execution_time', 'last_n': 100},
            {'position': 1, 'params': {},
             'metric': 'compile_time', 'last_n': 200},
        ]
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': cards},
            headers=self._manage_headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['cards']), 2)
        self.assertEqual(data['cards'][0]['metric'], 'execution_time')
        self.assertEqual(data['cards'][0]['last_n'], 100)
        self.assertEqual(data['cards'][1]['metric'], 'compile_time')

    def test_put_then_get(self):
        """PUT cards then GET returns the same cards."""
        cards = [
            {'position': 0, 'params': {'os': 'linux'},
             'metric': 'execution_time', 'last_n': 500},
        ]
        self.client.put(
            PREFIX + '/dashboard',
            json={'cards': cards},
            headers=self._manage_headers,
        )

        resp = self.client.get(PREFIX + '/dashboard')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['cards']), 1)
        self.assertEqual(data['cards'][0]['params'], {'os': 'linux'})
        self.assertEqual(data['cards'][0]['metric'], 'execution_time')

    def test_put_empty_replaces_all(self):
        """PUT with empty cards list clears the dashboard."""
        # First add some cards
        self.client.put(
            PREFIX + '/dashboard',
            json={'cards': [
                {'position': 0, 'params': {},
                 'metric': 'execution_time', 'last_n': 500},
            ]},
            headers=self._manage_headers,
        )

        # Now clear
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
            headers=self._manage_headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['cards']), 0)

        # GET should also show empty
        resp = self.client.get(PREFIX + '/dashboard')
        self.assertEqual(len(resp.get_json()['cards']), 0)


class TestDashboardAuth(unittest.TestCase):
    """Auth tests for dashboard endpoints."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_get_no_auth_allowed(self):
        """GET /dashboard requires read scope (available without auth)."""
        resp = self.client.get(PREFIX + '/dashboard')
        self.assertEqual(resp.status_code, 200)

    def test_put_no_auth_401(self):
        """PUT /dashboard without auth returns 401."""
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
        )
        self.assertEqual(resp.status_code, 401)

    def test_put_read_scope_403(self):
        """PUT with read scope returns 403."""
        headers = make_scoped_headers(self.app, 'read')
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_put_triage_scope_403(self):
        """PUT with triage scope returns 403."""
        headers = make_scoped_headers(self.app, 'triage')
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_put_manage_scope_200(self):
        """PUT with manage scope succeeds."""
        headers = make_scoped_headers(self.app, 'manage')
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)

    def test_put_admin_scope_200(self):
        """PUT with admin scope succeeds."""
        resp = self.client.put(
            PREFIX + '/dashboard',
            json={'cards': []},
            headers=admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
