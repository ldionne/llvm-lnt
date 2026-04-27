# Tests for the v5 trends endpoint (POST /api/v5/{ts}/trends).
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
from v5_test_helpers import (
    create_app, create_client,
    create_commit, create_run,
    create_test, create_sample,
)

TS = 'nts'
PREFIX = f'/api/v5/{TS}'


def _setup_trends_data(app, unique=None):
    """Create runs with two parameter sets, two tests, and several commits.

    Uses direct DB helpers for timestamp control.  All commits are assigned
    ordinals so they participate in trends queries.

    Returns a dict with metadata for assertions.
    """
    if unique is None:
        unique = uuid.uuid4().hex[:8]

    test1_name = f'trends-t1/{unique}'
    test2_name = f'trends-t2/{unique}'

    db = app.instance.get_database("default")
    session = db.make_session()
    ts = db.testsuite[TS]

    test1 = create_test(session, ts, name=test1_name)
    test2 = create_test(session, ts, name=test2_name)

    # Params set A: 3 commits, each with 2 tests
    # Commit ordinal 10000: test1=4.0, test2=16.0  -> geomean = 8.0
    # Commit ordinal 10001: test1=9.0, test2=9.0   -> geomean = 9.0
    # Commit ordinal 10002: test1=1.0, test2=100.0 -> geomean = 10.0
    params_a = {'compiler': f'clang-a-{unique}'}
    for i, (v1, v2) in enumerate([(4.0, 16.0), (9.0, 9.0), (1.0, 100.0)]):
        commit = create_commit(
            session, ts, commit=f'{100 + i}-{unique}')
        commit.ordinal = 10000 + i
        run = create_run(
            session, ts, commit,
            submitted_at=datetime.datetime(2024, 6, 1 + i, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc),
            run_parameters=params_a)
        create_sample(session, ts, run, test1, execution_time=v1)
        create_sample(session, ts, run, test2, execution_time=v2)

    # Params set B: 1 commit with 1 test (earlier ordinal)
    params_b = {'compiler': f'clang-b-{unique}'}
    commit_b = create_commit(
        session, ts, commit=f'200-{unique}')
    commit_b.ordinal = 9000
    run_b = create_run(
        session, ts, commit_b,
        submitted_at=datetime.datetime(2024, 5, 1, 12, 0, 0,
                                       tzinfo=datetime.timezone.utc),
        run_parameters=params_b)
    create_sample(session, ts, run_b, test1, execution_time=25.0)

    session.commit()
    session.close()

    return {
        'params_a': params_a,
        'params_b': params_b,
        'test1': test1_name,
        'test2': test2_name,
    }


def _setup_single_commit(app, *, values, commit_prefix, submitted_at,
                         ordinal, run_parameters=None):
    """Create two tests and one commit for edge-case testing.

    *values* is a dict mapping test suffix ('t1', 't2') to sample value.
    Returns the run_parameters used.
    """
    unique = uuid.uuid4().hex[:8]
    if run_parameters is None:
        run_parameters = {'env': f'{commit_prefix}-{unique}'}

    db = app.instance.get_database("default")
    session = db.make_session()
    ts = db.testsuite[TS]

    test1 = create_test(session, ts, name=f'trends-{commit_prefix}-t1/{unique}')
    test2 = create_test(session, ts, name=f'trends-{commit_prefix}-t2/{unique}')
    commit = create_commit(session, ts, commit=f'{commit_prefix}-{unique}')
    commit.ordinal = ordinal
    run = create_run(
        session, ts, commit, submitted_at=submitted_at,
        run_parameters=run_parameters)
    create_sample(session, ts, run, test1, execution_time=values['t1'])
    create_sample(session, ts, run, test2, execution_time=values['t2'])

    session.commit()
    session.close()
    return run_parameters


class TestTrendsErrors(unittest.TestCase):
    """Tests for error responses from the trends endpoint."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)

    def test_unknown_metric_returns_400(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'nonexistent_metric'})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_fields_rejected(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time', 'bogus_field': 'value'})
        self.assertEqual(resp.status_code, 422)

    def test_invalid_last_n_zero_returns_422(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time', 'last_n': 0})
        self.assertEqual(resp.status_code, 422)

    def test_invalid_last_n_negative_returns_422(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time', 'last_n': -1})
        self.assertEqual(resp.status_code, 422)

    def test_non_integer_last_n_returns_422(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time', 'last_n': 'abc'})
        self.assertEqual(resp.status_code, 422)

    def test_missing_metric_returns_422(self):
        resp = self.client.post(
            PREFIX + '/trends', json={})
        self.assertEqual(resp.status_code, 422)

    def test_non_numeric_metric_returns_400(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'hash'})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn('error', data)
        self.assertIn("'real'", data['error']['message'])

    def test_old_machine_param_rejected(self):
        """Sending the old machine parameter returns 422."""
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'machine': ['some-machine']})
        self.assertEqual(resp.status_code, 422)


class TestTrendsValidQuery(unittest.TestCase):
    """Tests for valid queries that return aggregated trends data."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._data = _setup_trends_data(cls.app)

    def test_returns_200(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': d['params_a']})
        self.assertEqual(resp.status_code, 200)

    def test_response_structure(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': d['params_a']})
        data = resp.get_json()
        self.assertIn('metric', data)
        self.assertEqual(data['metric'], 'execution_time')
        self.assertIn('items', data)
        self.assertIsInstance(data['items'], list)

    def test_item_structure(self):
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': d['params_a']})
        data = resp.get_json()
        self.assertGreater(len(data['items']), 0)
        item = data['items'][0]
        # New model: no 'machine' field
        self.assertNotIn('machine', item)
        self.assertIn('commit', item)
        self.assertIn('ordinal', item)
        self.assertIn('submitted_at', item)
        self.assertIn('value', item)
        self.assertIn('tag', item)
        self.assertIsInstance(item['commit'], str)
        self.assertIsNotNone(item['ordinal'])
        self.assertIsInstance(item['ordinal'], int)

    def test_geomean_correctness(self):
        """Verify geomean is computed correctly from known values."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': d['params_a']})
        data = resp.get_json()
        items = data['items']
        self.assertEqual(len(items), 3)

        values = [item['value'] for item in items]
        self.assertAlmostEqual(values[0], 8.0, places=5)
        self.assertAlmostEqual(values[1], 9.0, places=5)
        self.assertAlmostEqual(values[2], 10.0, places=5)

    def test_params_filter(self):
        """Only runs matching params are included."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': d['params_a']})
        data = resp.get_json()
        # params_a has 3 commits
        self.assertEqual(len(data['items']), 3)

    def test_no_params_filter_returns_all(self):
        """Omitting params returns data for all runs."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time'})
        data = resp.get_json()
        # Should include commits from both param sets
        # 3 from params_a + 1 from params_b = 4 commit groups
        ordinals = {item['ordinal'] for item in data['items']}
        self.assertIn(9000, ordinals)
        self.assertGreaterEqual(len(data['items']), 4)

    def test_last_n_filter(self):
        """last_n limits to the most recent N commits by ordinal."""
        d = self._data
        # last_n=3 should return ordinals 10000, 10001, 10002 (top 3)
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time', 'last_n': 3})
        data = resp.get_json()
        ordinals = {item['ordinal'] for item in data['items']}
        self.assertNotIn(9000, ordinals)

    def test_sorted_by_ordinal(self):
        """Items are sorted by ordinal ascending."""
        d = self._data
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time'})
        data = resp.get_json()
        ordinals = [item['ordinal'] for item in data['items']]
        self.assertEqual(ordinals, sorted(ordinals))

    def test_unordered_commits_excluded(self):
        """Commits without ordinals are excluded from trends results."""
        unique = uuid.uuid4().hex[:8]

        db = self.app.instance.get_database("default")
        session = db.make_session()
        ts = db.testsuite[TS]

        test = create_test(session, ts, name=f'trends-unord-t/{unique}')
        commit = create_commit(session, ts, commit=f'unord-{unique}')
        run = create_run(session, ts, commit,
                         run_parameters={'env': f'unord-{unique}'})
        create_sample(session, ts, run, test, execution_time=42.0)
        session.commit()
        session.close()

        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': {'env': f'unord-{unique}'}})
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(data['items']), 0)


class TestTrendsEdgeCases(unittest.TestCase):
    """Tests for edge cases: zero values, etc."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._params = _setup_single_commit(
            cls.app,
            values={'t1': 0.0, 't2': 25.0},
            commit_prefix='edge',
            ordinal=300,
            submitted_at=datetime.datetime(2024, 7, 1, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc))

    def test_geomean_excludes_zero_values(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': self._params})
        data = resp.get_json()
        self.assertEqual(len(data['items']), 1)
        self.assertAlmostEqual(data['items'][0]['value'], 25.0, places=5)


class TestTrendsAllZeroGroup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._params = _setup_single_commit(
            cls.app,
            values={'t1': 0.0, 't2': 0.0},
            commit_prefix='allzero',
            ordinal=400,
            submitted_at=datetime.datetime(2024, 8, 1, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc))

    def test_all_zero_group_excluded(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': self._params})
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(data['items']), 0)


class TestTrendsNegativeValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = create_app(sys.argv[1])
        cls.client = create_client(cls.app)
        cls._params = _setup_single_commit(
            cls.app,
            values={'t1': -5.0, 't2': 16.0},
            commit_prefix='neg',
            ordinal=500,
            submitted_at=datetime.datetime(2024, 8, 2, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc))

    def test_negative_values_excluded(self):
        resp = self.client.post(
            PREFIX + '/trends',
            json={'metric': 'execution_time',
                  'params': self._params})
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(data['items']), 1)
        self.assertAlmostEqual(data['items'][0]['value'], 16.0, places=5)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
