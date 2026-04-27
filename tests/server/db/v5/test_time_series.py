# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     python %s
# END.

import datetime
import os
import sys
import unittest

import sqlalchemy
import sqlalchemy.orm

from lnt.server.db.v5.schema import parse_schema
from lnt.server.db.v5.models import create_suite_models
from lnt.server.db.v5 import V5TestSuiteDB


def _make_engine():
    db_uri = os.environ.get('LNT_TEST_DB_URI')
    db_name = os.environ.get('LNT_TEST_DB_NAME')
    if not db_uri or not db_name:
        raise unittest.SkipTest(
            "LNT_TEST_DB_URI / LNT_TEST_DB_NAME not set")
    return sqlalchemy.create_engine(f"{db_uri}/{db_name}")


def _test_schema():
    return parse_schema({
        "name": "ts",
        "metrics": [
            {"name": "execution_time", "type": "real"},
        ],
        "commit_fields": [
            {"name": "author", "searchable": True},
        ],
    })


class TestTimeSeries(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.suite_models = create_suite_models(cls.schema)
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.suite_models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

        class _FakeV5DB:
            pass
        cls.tsdb = V5TestSuiteDB(_FakeV5DB(), cls.schema, cls.suite_models)

        # Seed test data
        session = cls.Session(expire_on_commit=False)

        cls.test = cls.tsdb.get_or_create_tests(session, ["ts-test/bench"])
        cls.test = session.query(cls.tsdb.Test).filter_by(
            name="ts-test/bench").one()

        # Create 5 commits with ordinals
        cls.commits = []
        for i in range(5):
            c = cls.tsdb.get_or_create_commit(
                session, f"commit-{i}", author=f"Author-{i}")
            c.ordinal = (i + 1) * 10  # 10, 20, 30, 40, 50
            cls.commits.append(c)
        session.flush()

        # Create a commit WITHOUT ordinal
        cls.unordered_commit = cls.tsdb.get_or_create_commit(
            session, "unordered-commit")
        session.flush()

        # Create runs and samples with run_parameters for filtering
        base_time = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        cls.runs = []
        for i, c in enumerate(cls.commits):
            run = cls.tsdb.create_run(
                session, commit=c,
                submitted_at=base_time + datetime.timedelta(hours=i),
                run_parameters={"compiler": "clang", "os": "linux"})
            cls.tsdb.create_samples(session, run, [{
                "test_id": cls.test.id,
                "execution_time": float(i + 1),
            }])
            cls.runs.append(run)

        # Run at unordered commit
        cls.unordered_run = cls.tsdb.create_run(
            session, commit=cls.unordered_commit,
            submitted_at=base_time + datetime.timedelta(hours=10),
            run_parameters={"compiler": "clang", "os": "linux"})
        cls.tsdb.create_samples(session, cls.unordered_run, [{
            "test_id": cls.test.id,
            "execution_time": 99.0,
        }])

        # Create a run with different params at the first commit
        cls.gcc_run = cls.tsdb.create_run(
            session, commit=cls.commits[0],
            submitted_at=base_time + datetime.timedelta(hours=20),
            run_parameters={"compiler": "gcc", "os": "linux"})
        cls.tsdb.create_samples(session, cls.gcc_run, [{
            "test_id": cls.test.id,
            "execution_time": 77.0,
        }])

        session.commit()
        session.close()

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_basic_query_no_params(self):
        """Query without params returns all runs."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time")
        # 5 ordered + 1 unordered + 1 gcc = 7 total
        self.assertEqual(len(results), 7)
        session.close()

    def test_params_filter_single_key(self):
        """Filter by a single param key."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "clang"})
        # 5 ordered clang + 1 unordered clang = 6
        self.assertEqual(len(results), 6)
        session.close()

    def test_params_filter_multi_key_and(self):
        """Different keys are AND-combined."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "clang", "os": "linux"})
        # All clang runs also have os=linux
        self.assertEqual(len(results), 6)
        session.close()

    def test_params_filter_same_key_or(self):
        """Multiple values for the same key are OR-combined."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": ["clang", "gcc"]})
        # All runs have either clang or gcc
        self.assertEqual(len(results), 7)
        session.close()

    def test_params_filter_excludes_nonmatching(self):
        """Params that don't match exclude runs."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "gcc"})
        # Only the gcc run at commit-0
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["value"], 77.0)
        session.close()

    def test_params_filter_no_match(self):
        """Params that don't match any run return empty results."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "msvc"})
        self.assertEqual(len(results), 0)
        session.close()

    def test_empty_params_matches_all(self):
        """Empty params dict matches all runs."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={})
        # Empty params -> same as no filter
        all_results = self.tsdb.query_time_series(
            session, self.test, "execution_time")
        self.assertEqual(len(results), len(all_results))
        session.close()

    def test_sort_by_ordinal(self):
        """Sorting by ordinal excludes unordered commits."""
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "clang"},
            sort="ordinal")
        # Only 5 commits with ordinals (clang)
        self.assertEqual(len(results), 5)
        ordinals = [r["ordinal"] for r in results]
        self.assertEqual(ordinals, [10, 20, 30, 40, 50])
        session.close()

    def test_commit_range(self):
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            params={"compiler": "clang"},
            commit_range=(20, 40))
        ordinals = [r["ordinal"] for r in results]
        self.assertEqual(len(results), 3)
        for o in ordinals:
            self.assertGreaterEqual(o, 20)
            self.assertLessEqual(o, 40)
        session.close()

    def test_time_range(self):
        session = self.Session()
        start = datetime.datetime(2024, 1, 1, 13, 0, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2024, 1, 1, 15, 0, 0, tzinfo=datetime.timezone.utc)
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            time_range=(start, end))
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertGreaterEqual(r["submitted_at"], start)
            self.assertLessEqual(r["submitted_at"], end)
        session.close()

    def test_create_run_without_commit_raises(self):
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.create_run(
                session, commit=None,
                submitted_at=datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc))
        session.close()

    def test_unknown_metric_raises(self):
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.query_time_series(
                session, self.test, "nonexistent_metric")
        session.close()

    def test_limit(self):
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            limit=2)
        self.assertEqual(len(results), 2)
        session.close()

    def test_result_structure(self):
        session = self.Session()
        results = self.tsdb.query_time_series(
            session, self.test, "execution_time",
            sort="ordinal", limit=1)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertIn("commit", r)
        self.assertIn("ordinal", r)
        self.assertIn("value", r)
        self.assertIn("run_id", r)
        self.assertIn("submitted_at", r)
        # No machine_name -- machine concept removed
        self.assertNotIn("machine_name", r)
        session.close()


class TestQueryTrends(unittest.TestCase):
    """Tests for V5TestSuiteDB.query_trends() geomean aggregation."""

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.suite_models = create_suite_models(cls.schema)
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.suite_models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

        class _FakeV5DB:
            pass
        cls.tsdb = V5TestSuiteDB(_FakeV5DB(), cls.schema, cls.suite_models)

        # Seed data: 3 commits, multiple tests per commit
        session = cls.Session(expire_on_commit=False)

        cls.tsdb.get_or_create_tests(
            session, ["trends/bench1", "trends/bench2"])
        cls.test1 = session.query(cls.tsdb.Test).filter_by(
            name="trends/bench1").one()
        cls.test2 = session.query(cls.tsdb.Test).filter_by(
            name="trends/bench2").one()

        cls.commits = []
        base_time = datetime.datetime(2024, 3, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        for i in range(3):
            c = cls.tsdb.get_or_create_commit(session, f"trends-commit-{i}")
            c.ordinal = (i + 1) * 10  # 10, 20, 30
            cls.commits.append(c)
        session.flush()

        # Create runs with params for all 3 commits
        for i, c in enumerate(cls.commits):
            run = cls.tsdb.create_run(
                session, commit=c,
                submitted_at=base_time + datetime.timedelta(hours=i),
                run_parameters={"compiler": "clang", "os": "linux"})
            # Two tests per run with known positive values
            cls.tsdb.create_samples(session, run, [
                {"test_id": cls.test1.id, "execution_time": 2.0},
                {"test_id": cls.test2.id, "execution_time": 8.0},
            ])

        # Create runs with different params (first 2 commits only)
        for i, c in enumerate(cls.commits[:2]):
            run = cls.tsdb.create_run(
                session, commit=c,
                submitted_at=base_time + datetime.timedelta(hours=i + 10),
                run_parameters={"compiler": "gcc", "os": "linux"})
            cls.tsdb.create_samples(session, run, [
                {"test_id": cls.test1.id, "execution_time": 4.0},
            ])

        session.commit()
        session.close()

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_basic_query_trends(self):
        """query_trends returns geomean-aggregated data grouped by commit."""
        session = self.Session()
        results = self.tsdb.query_trends(session, "execution_time")
        self.assertGreater(len(results), 0)
        # Check structure -- no machine_name
        r = results[0]
        self.assertIn("commit", r)
        self.assertIn("ordinal", r)
        self.assertIn("value", r)
        self.assertIn("submitted_at", r)
        self.assertNotIn("machine_name", r)
        session.close()

    def test_query_trends_with_params_filter(self):
        """Filter by params returns only matching data."""
        session = self.Session()
        results = self.tsdb.query_trends(
            session, "execution_time",
            params={"compiler": "clang"})
        # Clang has data at all 3 commits with values [2.0, 8.0]
        # geomean(2, 8) = sqrt(16) = 4.0
        for r in results:
            self.assertAlmostEqual(r["value"], 4.0, places=5)
        self.assertEqual(len(results), 3)
        session.close()

    def test_query_trends_geomean_value(self):
        """Verify the geomean is computed correctly for clang runs."""
        session = self.Session()
        results = self.tsdb.query_trends(
            session, "execution_time",
            params={"compiler": "clang"})
        # geomean(2, 8) = sqrt(16) = 4.0
        for r in results:
            self.assertAlmostEqual(r["value"], 4.0, places=5)
        self.assertEqual(len(results), 3)
        session.close()

    def test_query_trends_last_n(self):
        """last_n limits to the most recent N commits by ordinal."""
        session = self.Session()
        # Ordinals: 10, 20, 30.  last_n=2 -> ordinals 20 and 30.
        results = self.tsdb.query_trends(
            session, "execution_time", last_n=2)
        ordinals = {r["ordinal"] for r in results}
        self.assertEqual(ordinals, {20, 30})
        session.close()

    def test_query_trends_last_n_one(self):
        """last_n=1 returns only the single most recent commit."""
        session = self.Session()
        results = self.tsdb.query_trends(
            session, "execution_time", last_n=1)
        ordinals = {r["ordinal"] for r in results}
        self.assertEqual(ordinals, {30})
        session.close()

    def test_query_trends_last_n_exceeds_available(self):
        """last_n larger than available commits returns all data."""
        session = self.Session()
        all_results = self.tsdb.query_trends(session, "execution_time")
        limited_results = self.tsdb.query_trends(
            session, "execution_time", last_n=100)
        self.assertEqual(len(limited_results), len(all_results))
        session.close()

    def test_query_trends_last_n_none_returns_all(self):
        """Omitting last_n returns all data (no filtering by count)."""
        session = self.Session()
        results = self.tsdb.query_trends(session, "execution_time")
        # 3 commits total (grouped by commit, not by machine anymore)
        self.assertEqual(len(results), 3)
        session.close()

    def test_query_trends_excludes_unordered_commits(self):
        """Commits without ordinals are excluded from trends results."""
        session = self.Session()
        unordered = self.tsdb.get_or_create_commit(session, "trends-unordered")
        # ordinal remains None
        run = self.tsdb.create_run(
            session, commit=unordered,
            submitted_at=datetime.datetime(2024, 3, 2, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc),
            run_parameters={"compiler": "clang"})
        self.tsdb.create_samples(session, run, [
            {"test_id": self.test1.id, "execution_time": 99.0},
        ])
        session.flush()

        results = self.tsdb.query_trends(session, "execution_time")
        for r in results:
            self.assertIsNotNone(r["ordinal"])
        commits = {r["commit"] for r in results}
        self.assertNotIn("trends-unordered", commits)
        session.close()

    def test_query_trends_unknown_metric_raises(self):
        """Unknown metric name raises ValueError."""
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.query_trends(session, "nonexistent_metric")
        session.close()

    def test_query_trends_ordered_by_ordinal(self):
        """Results are ordered by ordinal."""
        session = self.Session()
        results = self.tsdb.query_trends(
            session, "execution_time",
            params={"compiler": "clang"})
        ordinals = [r["ordinal"] for r in results]
        self.assertEqual(ordinals, sorted(ordinals))
        session.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
