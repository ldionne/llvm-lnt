# Tests for the v5 test entity endpoints.
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
    create_app, create_client, collect_all_pages, submit_run,
)


TS = 'nts'
PREFIX = f'/api/v5/{TS}'


class TestTestList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_list_returns_200(self):
        resp = self.client.get(PREFIX + '/tests')
        self.assertEqual(resp.status_code, 200)

    def test_list_has_pagination_envelope(self):
        resp = self.client.get(PREFIX + '/tests')
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertIn('cursor', data)

    def test_list_with_data(self):
        unique = uuid.uuid4().hex[:8]
        name = f'list-test-{unique}'
        submit_run(self.client, f'rev-{unique}',
                   [{'name': name, 'execution_time': [1.0]}])

        resp = self.client.get(
            PREFIX + f'/tests?search=list-test-{unique}')
        data = resp.get_json()
        names = [t['name'] for t in data['items']]
        self.assertIn(name, names)

    def test_invalid_cursor_returns_400(self):
        resp = self.client.get(
            PREFIX + '/tests?cursor=not-a-valid-cursor!!!')
        self.assertEqual(resp.status_code, 400)


class TestTestFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_filter_search(self):
        unique = uuid.uuid4().hex[:8]
        prefix = f'search-{unique}'
        name = f'{prefix}-test'
        submit_run(self.client, f'rev-{unique}',
                   [{'name': name, 'execution_time': [1.0]}])

        resp = self.client.get(
            PREFIX + f'/tests?search={prefix}')
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        for t in data['items']:
            self.assertIn(prefix, t['name'])

    def test_filter_search_case_insensitive(self):
        unique = uuid.uuid4().hex[:8]
        name = f'CaSe-TeSt-{unique}'
        submit_run(self.client, f'rev-{unique}',
                   [{'name': name, 'execution_time': [1.0]}])

        resp = self.client.get(
            PREFIX + f'/tests?search=case-test-{unique}')
        data = resp.get_json()
        names = [t['name'] for t in data['items']]
        self.assertIn(name, names)

    def test_filter_no_match(self):
        resp = self.client.get(
            PREFIX + '/tests?search=zzzz_no_match_xyz_9999')
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)


class TestTestPagination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._prefix = f'pag-{uuid.uuid4().hex[:8]}'
        for i in range(5):
            submit_run(cls.client,
                       f'rev-{cls._prefix}-{i}',
                       [{'name': f'{cls._prefix}-test-{i}',
                         'execution_time': [1.0]}])

    def _collect_all_pages(self):
        url = PREFIX + f'/tests?search={self._prefix}&limit=2'
        return collect_all_pages(self, self.client, url)

    def test_pagination_collects_all_items(self):
        all_items = self._collect_all_pages()
        self.assertEqual(len(all_items), 5)

    def test_no_duplicate_items_across_pages(self):
        all_items = self._collect_all_pages()
        names = [item['name'] for item in all_items]
        self.assertEqual(len(names), len(set(names)))


class TestTestUnknownParams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_tests_list_unknown_param_returns_400(self):
        resp = self.client.get(PREFIX + '/tests?bogus=1')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('bogus', data['error']['message'])

    def test_param_prefix_accepted(self):
        """param.* query parameters are accepted."""
        resp = self.client.get(PREFIX + '/tests?param.os=linux')
        self.assertEqual(resp.status_code, 200)


class TestTestParamMetricFilter(unittest.TestCase):
    """Tests for param.* and metric= filters on GET /tests."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._prefix = uuid.uuid4().hex[:8]

        # Param set A has data for test_1 and test_2.
        cls.param_a = f'envA-{cls._prefix}'
        cls.test_1 = f'mf-test1-{cls._prefix}'
        cls.test_2 = f'mf-test2-{cls._prefix}'

        submit_run(cls.client, f'700-{cls._prefix}',
                   [{'name': cls.test_1, 'execution_time': [1.0]},
                    {'name': cls.test_2, 'execution_time': [2.0]}],
                   run_parameters={'env': cls.param_a})

        submit_run(cls.client, f'702-{cls._prefix}',
                   [{'name': cls.test_1, 'execution_time': [1.1]},
                    {'name': cls.test_2, 'execution_time': [2.1]}],
                   run_parameters={'env': cls.param_a})

        # Param set B has data for test_2 and test_3
        cls.param_b = f'envB-{cls._prefix}'
        cls.test_3 = f'mf-test3-{cls._prefix}'

        submit_run(cls.client, f'701-{cls._prefix}',
                   [{'name': cls.test_2, 'execution_time': [3.0]},
                    {'name': cls.test_3, 'execution_time': [4.0]}],
                   run_parameters={'env': cls.param_b})

        # test_4 has no execution_time samples -- compile_time only
        cls.test_4 = f'mf-test4-{cls._prefix}'
        submit_run(cls.client, f'703-{cls._prefix}',
                   [{'name': cls.test_4, 'compile_time': [0.5]}],
                   run_parameters={'env': f'envC-{cls._prefix}'})

    def test_filter_by_param_a(self):
        resp = self.client.get(
            PREFIX + f'/tests?param.env={self.param_a}'
            f'&search=mf-test')
        self.assertEqual(resp.status_code, 200)
        names = {t['name'] for t in resp.get_json()['items']}
        self.assertIn(self.test_1, names)
        self.assertIn(self.test_2, names)

    def test_filter_by_param_b(self):
        resp = self.client.get(
            PREFIX + f'/tests?param.env={self.param_b}'
            f'&search=mf-test')
        self.assertEqual(resp.status_code, 200)
        names = {t['name'] for t in resp.get_json()['items']}
        self.assertIn(self.test_2, names)
        self.assertIn(self.test_3, names)

    def test_filter_by_metric(self):
        resp = self.client.get(
            PREFIX + '/tests?metric=execution_time'
            '&search=mf-test')
        self.assertEqual(resp.status_code, 200)
        names = {t['name'] for t in resp.get_json()['items']}
        self.assertIn(self.test_1, names)
        self.assertIn(self.test_2, names)
        self.assertIn(self.test_3, names)
        self.assertNotIn(self.test_4, names)

    def test_filter_by_param_and_metric(self):
        resp = self.client.get(
            PREFIX + f'/tests?param.env={self.param_a}'
            f'&metric=execution_time&search=mf-test')
        self.assertEqual(resp.status_code, 200)
        names = {t['name'] for t in resp.get_json()['items']}
        self.assertEqual(names, {self.test_1, self.test_2})

    def test_unknown_metric_returns_400(self):
        resp = self.client.get(
            PREFIX + '/tests?metric=nonexistent_metric')
        self.assertEqual(resp.status_code, 400)

    def test_multiple_samples_deduplicated(self):
        """Param A has two runs with samples for test_1 -- test_1
        should still appear only once (DISTINCT)."""
        resp = self.client.get(
            PREFIX + f'/tests?param.env={self.param_a}'
            f'&search=mf-test1-{self._prefix}')
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()['items']
        names = [t['name'] for t in items]
        self.assertEqual(names, [self.test_1])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
