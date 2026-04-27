# Tests for the v5 samples query endpoint (POST /api/v5/{ts}/samples).
#
# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     %{utils}/with_temporary_instance.py --db-version 5.0 %t.instance \
# RUN:         -- python %s %t.instance
# END.

import datetime
import sys
import os
import unittest
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from v5_test_helpers import create_app, create_client, set_ordinal, submit_run

TS = 'nts'
PREFIX = f'/api/v5/{TS}'


def _setup_samples_data(client, app, test_name, num_points=5,
                        run_parameters=None):
    """Create test data with runs and samples.

    Returns a dict with the created entities for assertions.
    """
    rev_prefix = uuid.uuid4().hex[:6]
    ordinal_base = int(uuid.uuid4().hex[:6], 16)
    run_uuids = []
    commits = []
    for i in range(num_points):
        commit_str = f'{100 + i}-{rev_prefix}'
        data = submit_run(
            client, commit_str,
            [{'name': test_name, 'execution_time': [float(i + 1) * 1.5]}],
            run_parameters=run_parameters)
        run_uuids.append(data['run_uuid'])
        commits.append(commit_str)

    for i, commit_str in enumerate(commits):
        set_ordinal(client, commit_str, ordinal_base + i)

    # Set sequential timestamps via direct DB
    db = app.instance.get_database("default")
    session = db.make_session()
    ts = db.testsuite[TS]
    for i, run_uuid in enumerate(run_uuids):
        run = ts.get_run(session, uuid=run_uuid)
        run.submitted_at = datetime.datetime(
            2024, 1, 1 + i, 12, 0, 0, tzinfo=datetime.timezone.utc)
    session.commit()
    session.close()

    return {
        'test': test_name,
        'run_uuids': run_uuids,
        'num_points': num_points,
        'commits': commits,
    }


class TestSamplesQueryNotFound(unittest.TestCase):
    """Tests for error responses."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_nonexistent_test_returns_empty(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': ['nonexistent-test-xyz'],
                  'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)

    def test_nonexistent_metric_returns_400(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'metric': 'nonexistent_field'})
        self.assertEqual(resp.status_code, 400)


class TestSamplesQueryValid(unittest.TestCase):
    """Tests for valid queries that return data."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-valid-t/{unique}',
            num_points=5,
            run_parameters={'compiler': 'clang'},
        )

    def test_returns_200(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)

    def test_returns_items(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        self.assertIn('items', data)
        self.assertEqual(len(data['items']), d['num_points'])

    def test_data_point_structure_with_metric(self):
        """When metric is specified, response has flat metric value."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        for item in data['items']:
            self.assertIn('test', item)
            self.assertIn('execution_time', item)
            self.assertIn('commit', item)
            self.assertIn('run_uuid', item)
            self.assertIn('submitted_at', item)
            self.assertIsInstance(item['execution_time'], (int, float))
            self.assertIsInstance(item['commit'], str)
            self.assertNotIn('metrics', item)

    def test_data_point_structure_without_metric(self):
        """When metric is omitted, response has metrics dict."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']]})
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        for item in data['items']:
            self.assertIn('test', item)
            self.assertIn('metrics', item)
            self.assertIsInstance(item['metrics'], dict)
            self.assertIn('commit', item)
            self.assertIn('run_uuid', item)

    def test_run_uuids_are_valid(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        returned_uuids = {item['run_uuid'] for item in data['items']}
        expected_uuids = set(d['run_uuids'])
        self.assertEqual(returned_uuids, expected_uuids)

    def test_values_are_correct(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        values = sorted([item['execution_time'] for item in data['items']])
        expected = sorted([float(i + 1) * 1.5 for i in range(d['num_points'])])
        self.assertEqual(values, expected)

    def test_cursor_envelope(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        self.assertIn('cursor', data)
        self.assertIn('next', data['cursor'])
        self.assertIn('previous', data['cursor'])

    def test_no_auth_required_for_read(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)

    def test_params_filter(self):
        """Filtering by params returns only matching runs."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'params': {'compiler': 'clang'}})
        data = resp.get_json()
        self.assertEqual(len(data['items']), d['num_points'])

    def test_params_filter_no_match(self):
        """Params filter with non-matching value returns empty."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'params': {'compiler': 'gcc'}})
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)

    def test_commit_filter(self):
        """Filter by exact commit returns data for that commit."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'commit': d['commits'][2]})
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['commit'], d['commits'][2])

    def test_run_filter(self):
        """Filter by run UUID returns data for that run."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'run': d['run_uuids'][0]})
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['run_uuid'], d['run_uuids'][0])


class TestSamplesQueryOrdering(unittest.TestCase):
    """Tests for sort parameter."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-order-t/{unique}',
            num_points=5,
        )

    def test_sort_by_ordinal(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'sort': 'ordinal'})
        data = resp.get_json()
        ordinals = [item['ordinal'] for item in data['items']]
        self.assertEqual(ordinals, sorted(ordinals))

    def test_sort_descending_ordinal(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'sort': '-ordinal'})
        data = resp.get_json()
        ordinals = [item['ordinal'] for item in data['items']]
        self.assertEqual(ordinals, sorted(ordinals, reverse=True))

    def test_sort_invalid_field_returns_400(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'metric': 'execution_time', 'sort': 'invalid_field'})
        self.assertEqual(resp.status_code, 400)


class TestSamplesQueryPagination(unittest.TestCase):
    """Tests for cursor-based pagination."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-page-t/{unique}',
            num_points=7,
        )

    def test_pagination_collects_all_items(self):
        d = self._data
        all_items = []
        params = {'test': [d['test']], 'metric': 'execution_time', 'limit': 3}

        resp = self.client.post(PREFIX + '/samples', json=params)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        all_items.extend(data['items'])
        cursor = data['cursor']['next']

        pages = 1
        while cursor:
            resp = self.client.post(
                PREFIX + '/samples', json={**params, 'cursor': cursor})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            all_items.extend(data['items'])
            cursor = data['cursor']['next']
            pages += 1
            if pages > 10:
                self.fail("Too many pages; infinite loop detected")

        self.assertEqual(len(all_items), d['num_points'])

    def test_no_duplicate_items_across_pages(self):
        d = self._data
        all_uuids = []
        params = {'test': [d['test']], 'metric': 'execution_time', 'limit': 2}

        resp = self.client.post(PREFIX + '/samples', json=params)
        data = resp.get_json()
        all_uuids.extend(item['run_uuid'] for item in data['items'])
        cursor = data['cursor']['next']

        pages = 1
        while cursor:
            resp = self.client.post(
                PREFIX + '/samples', json={**params, 'cursor': cursor})
            data = resp.get_json()
            all_uuids.extend(item['run_uuid'] for item in data['items'])
            cursor = data['cursor']['next']
            pages += 1
            if pages > 10:
                break

        self.assertEqual(len(all_uuids), len(set(all_uuids)))

    def test_invalid_cursor_returns_400(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'metric': 'execution_time',
                  'cursor': 'not-a-valid-cursor!!!'})
        self.assertEqual(resp.status_code, 400)


class TestSamplesQueryLimit(unittest.TestCase):
    """Tests for the limit parameter."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-limit-t/{unique}',
            num_points=10,
        )

    def test_limit_reduces_results(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'limit': 3})
        data = resp.get_json()
        self.assertEqual(len(data['items']), 3)

    def test_limit_with_next_cursor(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'limit': 3})
        data = resp.get_json()
        self.assertIsNotNone(data['cursor']['next'])

    def test_default_limit_is_5000(self):
        """Default limit is 5000, not the old 100."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time'})
        data = resp.get_json()
        # All 10 items returned (less than default limit of 5000)
        self.assertEqual(len(data['items']), d['num_points'])
        self.assertIsNone(data['cursor']['next'])


class TestSamplesQueryTimeRange(unittest.TestCase):
    """Tests for submitted_before/submitted_after filtering."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-time-t/{unique}',
            num_points=10,
        )

    def test_submitted_after_filter(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'submitted_after': '2024-01-06T00:00:00'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        for item in data['items']:
            self.assertGreater(item['submitted_at'], '2024-01-06T00:00:00Z')

    def test_submitted_before_filter(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [d['test']], 'metric': 'execution_time',
                  'submitted_before': '2024-01-04T00:00:00'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        for item in data['items']:
            self.assertLess(item['submitted_at'], '2024-01-04T00:00:00Z')

    def test_malformed_submitted_after_returns_400(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'metric': 'execution_time',
                  'submitted_after': 'not-a-date'})
        self.assertEqual(resp.status_code, 400)


class TestSamplesQueryUnknownParams(unittest.TestCase):
    """Test that unknown JSON body fields are rejected with 422."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_single_unknown_param_returns_422(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'metric': 'execution_time', 'bogus': 'value'})
        self.assertEqual(resp.status_code, 422)

    def test_empty_body_returns_200(self):
        """Empty body (no metric) returns all samples."""
        resp = self.client.post(PREFIX + '/samples', json={})
        self.assertEqual(resp.status_code, 200)


class TestSamplesQueryMultiTest(unittest.TestCase):
    """Tests for multi-value test list (disjunction)."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._prefix = uuid.uuid4().hex[:8]
        rev_prefix = uuid.uuid4().hex[:6]

        cls.test_a = f'mv-test-alpha-{cls._prefix}'
        cls.test_b = f'mv-test-beta-{cls._prefix}'
        cls.test_c = f'mv-test-gamma-{cls._prefix}'

        for i, tname in enumerate([cls.test_a, cls.test_b, cls.test_c]):
            submit_run(
                cls.client, f'{500 + i}-{rev_prefix}',
                [{'name': tname, 'execution_time': [float(i + 1) * 2.0]}],
            )

        ordinal_base = int(uuid.uuid4().hex[:6], 16)
        for i in range(3):
            set_ordinal(cls.client, f'{500 + i}-{rev_prefix}',
                        ordinal_base + i)

    def test_single_test_param(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [self.test_a], 'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        tests = {item['test'] for item in data['items']}
        self.assertEqual(tests, {self.test_a})

    def test_two_test_params(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': [self.test_a, self.test_b],
                  'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        tests = {item['test'] for item in data['items']}
        self.assertEqual(tests, {self.test_a, self.test_b})

    def test_all_unknown_returns_empty(self):
        resp = self.client.post(
            PREFIX + '/samples',
            json={'test': ['nonexistent-1', 'nonexistent-2'],
                  'metric': 'execution_time'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['items']), 0)


class TestSamplesQuerySortPagination(unittest.TestCase):
    """Sort order is preserved across cursor pages."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        rev_prefix = uuid.uuid4().hex[:6]
        cls.test_names = [f'sort-page-a/{unique}',
                          f'sort-page-b/{unique}',
                          f'sort-page-c/{unique}']

        ordinal_base = int(uuid.uuid4().hex[:6], 16)
        for i in range(5):
            tests = [
                {'name': tn, 'execution_time': [float((i + 1) * 10 + j)]}
                for j, tn in enumerate(cls.test_names)
            ]
            commit_str = f'{600 + i}-{rev_prefix}'
            submit_run(cls.client, commit_str, tests)
            set_ordinal(cls.client, commit_str, ordinal_base + i)

    def test_sort_test_pagination(self):
        all_test_names = []
        params = {'metric': 'execution_time', 'sort': 'test,ordinal',
                  'limit': 4}

        resp = self.client.post(PREFIX + '/samples', json=params)
        data = resp.get_json()
        all_test_names.extend(item['test'] for item in data['items'])
        cursor = data['cursor']['next']

        pages = 1
        while cursor:
            resp = self.client.post(
                PREFIX + '/samples', json={**params, 'cursor': cursor})
            data = resp.get_json()
            all_test_names.extend(item['test'] for item in data['items'])
            cursor = data['cursor']['next']
            pages += 1
            if pages > 20:
                break

        self.assertEqual(all_test_names, sorted(all_test_names))


class TestSamplesQuerySubmittedAtPagination(unittest.TestCase):
    """Cursor pagination with sort=submitted_at does not crash."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        unique = uuid.uuid4().hex[:8]
        cls._data = _setup_samples_data(
            cls.client,
            cls.app,
            test_name=f'samples-subat-t/{unique}',
            num_points=5,
        )

    def test_submitted_at_sort_pagination(self):
        """Paginating with sort=submitted_at must not crash on cursor encode."""
        d = self._data
        all_items = []
        params = {'test': [d['test']], 'metric': 'execution_time',
                  'sort': 'submitted_at', 'limit': 2}

        resp = self.client.post(PREFIX + '/samples', json=params)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        all_items.extend(data['items'])
        cursor = data['cursor']['next']

        pages = 1
        while cursor:
            resp = self.client.post(
                PREFIX + '/samples', json={**params, 'cursor': cursor})
            self.assertEqual(resp.status_code, 200,
                             f"Page {pages + 1} failed: {resp.get_data(as_text=True)}")
            data = resp.get_json()
            all_items.extend(data['items'])
            cursor = data['cursor']['next']
            pages += 1
            if pages > 10:
                self.fail("Too many pages; infinite loop detected")

        self.assertEqual(len(all_items), d['num_points'])

        # Verify ordering is ascending by submitted_at
        timestamps = [item['submitted_at'] for item in all_items]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_submitted_at_desc_pagination(self):
        """Descending submitted_at pagination also works."""
        d = self._data
        all_items = []
        params = {'test': [d['test']], 'metric': 'execution_time',
                  'sort': '-submitted_at', 'limit': 2}

        resp = self.client.post(PREFIX + '/samples', json=params)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        all_items.extend(data['items'])
        cursor = data['cursor']['next']

        pages = 1
        while cursor:
            resp = self.client.post(
                PREFIX + '/samples', json={**params, 'cursor': cursor})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            all_items.extend(data['items'])
            cursor = data['cursor']['next']
            pages += 1
            if pages > 10:
                self.fail("Too many pages; infinite loop detected")

        self.assertEqual(len(all_items), d['num_points'])

        timestamps = [item['submitted_at'] for item in all_items]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
