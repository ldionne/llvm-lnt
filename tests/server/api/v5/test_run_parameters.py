# Tests for the v5 run parameter endpoints.
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
    create_app, create_client, submit_run,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


class TestRunParameterKeys(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/run-parameters."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._tag = uuid.uuid4().hex[:8]
        # Submit runs with specific parameters to seed the key/value tables
        submit_run(cls.client, f'rp-rev1-{cls._tag}',
                   [{'name': 'rp/test', 'execution_time': 1.0}],
                   run_parameters={f'rpkey_{cls._tag}': f'val1_{cls._tag}'})
        submit_run(cls.client, f'rp-rev2-{cls._tag}',
                   [{'name': 'rp/test', 'execution_time': 2.0}],
                   run_parameters={f'rpkey_{cls._tag}': f'val2_{cls._tag}',
                                   f'rpother_{cls._tag}': 'x'})

    def test_list_keys_returns_200(self):
        resp = self.client.get(PREFIX + '/run-parameters')
        self.assertEqual(resp.status_code, 200)

    def test_list_keys_has_items(self):
        resp = self.client.get(PREFIX + '/run-parameters')
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertIsInstance(data['items'], list)

    def test_list_keys_contains_seeded_keys(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters?search=rpkey_{self._tag}')
        data = resp.get_json()
        keys = [item['key'] for item in data['items']]
        self.assertIn(f'rpkey_{self._tag}', keys)

    def test_list_keys_search_prefix(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters?search=rpother_{self._tag}')
        data = resp.get_json()
        keys = [item['key'] for item in data['items']]
        self.assertIn(f'rpother_{self._tag}', keys)
        self.assertNotIn(f'rpkey_{self._tag}', keys)

    def test_list_keys_search_no_match(self):
        resp = self.client.get(
            PREFIX + '/run-parameters?search=zzz_nonexistent_key')
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)

    def test_list_keys_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/run-parameters?bogus=1')
        self.assertEqual(resp.status_code, 400)


class TestRunParameterValues(unittest.TestCase):
    """Tests for GET /api/v5/{ts}/run-parameters/{key}/values."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._tag = uuid.uuid4().hex[:8]
        cls._key = f'pvkey_{cls._tag}'
        submit_run(cls.client, f'pv-rev1-{cls._tag}',
                   [{'name': 'pv/test', 'execution_time': 1.0}],
                   run_parameters={cls._key: f'alpha_{cls._tag}'})
        submit_run(cls.client, f'pv-rev2-{cls._tag}',
                   [{'name': 'pv/test', 'execution_time': 2.0}],
                   run_parameters={cls._key: f'beta_{cls._tag}'})

    def test_list_values_returns_200(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters/{self._key}/values')
        self.assertEqual(resp.status_code, 200)

    def test_list_values_has_items(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters/{self._key}/values')
        data = resp.get_json()
        self.assertIn('items', data)
        values = [item['value'] for item in data['items']]
        self.assertIn(f'alpha_{self._tag}', values)
        self.assertIn(f'beta_{self._tag}', values)

    def test_list_values_search_prefix(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters/{self._key}/values'
            f'?search=alpha_{self._tag}')
        data = resp.get_json()
        values = [item['value'] for item in data['items']]
        self.assertIn(f'alpha_{self._tag}', values)
        self.assertNotIn(f'beta_{self._tag}', values)

    def test_list_values_nonexistent_key(self):
        """Values for a nonexistent key returns empty list (not 404)."""
        resp = self.client.get(
            PREFIX + '/run-parameters/zzz_nonexistent_key/values')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)

    def test_list_values_unknown_param_returns_400(self):
        resp = self.client.get(
            PREFIX + f'/run-parameters/{self._key}/values?bogus=1')
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
