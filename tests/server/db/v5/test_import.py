# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     python %s
# END.

import os
import sys
import unittest

import json

import sqlalchemy
import sqlalchemy.orm

from lnt.server.db.v5.schema import parse_schema
from lnt.server.db.v5.models import (
    V5Schema,
    V5SchemaVersion,
    create_global_tables,
    create_suite_models,
)
from lnt.server.db.v5 import V5DB, V5TestSuiteDB


def _make_engine():
    db_uri = os.environ.get('LNT_TEST_DB_URI')
    db_name = os.environ.get('LNT_TEST_DB_NAME')
    if not db_uri or not db_name:
        raise unittest.SkipTest(
            "LNT_TEST_DB_URI / LNT_TEST_DB_NAME not set")
    return sqlalchemy.create_engine(f"{db_uri}/{db_name}")


def _test_schema():
    return parse_schema({
        "name": "imp",
        "metrics": [
            {"name": "compile_time", "type": "real"},
            {"name": "execution_time", "type": "real"},
        ],
        "commit_fields": [
            {"name": "git_sha", "searchable": True},
            {"name": "author", "searchable": True},
        ],
    })


class _ImportTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.suite_models = create_suite_models(cls.schema)
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.suite_models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

        # Build a lightweight V5TestSuiteDB (we don't need the full V5DB)
        class _FakeV5DB:
            pass
        cls.tsdb = V5TestSuiteDB(_FakeV5DB(), cls.schema, cls.suite_models)

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()


class TestImportRun(_ImportTestBase):

    def test_import_with_commit(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "abc123",
            "commit_fields": {
                "git_sha": "abc123def456789",
                "author": "Jane Doe",
            },
            "run_parameters": {
                "build_config": "Release",
                "compiler": "clang-21",
            },
            "tests": [
                {
                    "name": "test.suite/benchmark1",
                    "compile_time": 1.23,
                    "execution_time": 0.45,
                },
                {
                    "name": "test.suite/benchmark2",
                    "execution_time": 0.67,
                },
            ],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        self.assertIsNotNone(run.id)
        self.assertIsNotNone(run.uuid)
        self.assertIsNotNone(run.commit_id)
        self.assertEqual(run.run_parameters, {
            "build_config": "Release",
            "compiler": "clang-21",
        })

        # Verify commit was created
        commit = self.tsdb.get_commit(session, id=run.commit_id)
        self.assertEqual(commit.commit, "abc123")
        self.assertEqual(commit.git_sha, "abc123def456789")
        self.assertEqual(commit.author, "Jane Doe")
        self.assertIsNone(commit.ordinal)

        # Verify samples
        samples = (
            session.query(self.suite_models.Sample)
            .filter_by(run_id=run.id)
            .all()
        )
        self.assertEqual(len(samples), 2)

        # Verify parameter keys and values were upserted
        keys = self.tsdb.list_parameter_keys(session)
        key_names = [k.key for k in keys]
        self.assertIn("build_config", key_names)
        self.assertIn("compiler", key_names)
        session.close()

    def test_import_without_commit(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "tests": [
                {"name": "test.suite/standalone", "execution_time": 0.1},
            ],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_missing_format_version(self):
        """format_version is required -- omitting it must raise."""
        session = self.Session()
        data = {
            "commit": "no-fmt-commit",
            "tests": [{"name": "test/fmt", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_wrong_format_version(self):
        """format_version must be '5' -- anything else must raise."""
        session = self.Session()
        data = {
            "format_version": "4",
            "commit": "wrong-fmt-commit",
            "tests": [{"name": "test/fmt2", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_same_commit_twice(self):
        """Second import at same commit should reuse the commit (first-write-wins)."""
        session = self.Session()
        data1 = {
            "format_version": "5",
            "commit": "same-commit",
            "commit_fields": {"author": "First"},
            "tests": [{"name": "test/a", "compile_time": 1.0}],
        }
        run1 = self.tsdb.import_run(session, data1)
        session.commit()

        data2 = {
            "format_version": "5",
            "commit": "same-commit",
            "commit_fields": {"author": "Second"},
            "tests": [{"name": "test/a", "compile_time": 2.0}],
        }
        run2 = self.tsdb.import_run(session, data2)
        session.commit()

        # Same commit reused
        self.assertEqual(run1.commit_id, run2.commit_id)

        # First-write-wins: author should still be "First"
        commit = self.tsdb.get_commit(session, id=run1.commit_id)
        self.assertEqual(commit.author, "First")
        session.close()

    def test_import_missing_test_name(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "missing-test-name-commit",
            "tests": [{"compile_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_empty_tests_list(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "empty-tests-commit",
            "tests": [],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        self.assertIsNotNone(run.id)
        samples = (
            session.query(self.suite_models.Sample)
            .filter_by(run_id=run.id)
            .all()
        )
        self.assertEqual(len(samples), 0)
        session.close()

    def test_import_unknown_top_level_keys_ignored(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "unknown-keys-commit",
            "tests": [{"name": "test/unk", "compile_time": 1.0}],
            "unknown_key": "should be ignored",
            "another_unknown": 42,
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        self.assertIsNotNone(run.id)
        session.close()

    def test_import_empty_run_parameters(self):
        """Omitting run_parameters defaults to {}."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "empty-params-commit",
            "tests": [{"name": "test/empty-params", "execution_time": 1.0}],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()
        self.assertEqual(run.run_parameters, {})
        session.close()

    def test_import_array_metric_creates_multiple_samples(self):
        """Array metric values unpack into multiple Sample rows."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "array-commit",
            "tests": [{
                "name": "test/array",
                "compile_time": [1.0, 2.0, 3.0],
                "execution_time": [0.1, 0.2, 0.3],
            }],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        samples = self.tsdb.list_samples(session, run_id=run.id)
        self.assertEqual(len(samples), 3)
        compile_times = sorted(s.compile_time for s in samples)
        exec_times = sorted(s.execution_time for s in samples)
        self.assertEqual(compile_times, [1.0, 2.0, 3.0])
        self.assertEqual(exec_times, [0.1, 0.2, 0.3])
        session.close()

    def test_import_mixed_scalar_and_array_metrics(self):
        """Scalar metrics are repeated across array-expanded samples."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "mixed-commit",
            "tests": [{
                "name": "test/mixed",
                "execution_time": [1.0, 2.0],
                "compile_time": 0.5,
            }],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        samples = self.tsdb.list_samples(session, run_id=run.id)
        self.assertEqual(len(samples), 2)
        for s in samples:
            self.assertEqual(s.compile_time, 0.5)
        exec_times = sorted(s.execution_time for s in samples)
        self.assertEqual(exec_times, [1.0, 2.0])
        session.close()

    def test_import_array_inconsistent_lengths_rejected(self):
        """Array metrics with different lengths raise ValueError."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "incon-commit",
            "tests": [{
                "name": "test/incon",
                "execution_time": [1.0, 2.0],
                "compile_time": [1.0, 2.0, 3.0],
            }],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_empty_array_rejected(self):
        """Empty metric arrays raise ValueError."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "empty-arr-commit",
            "tests": [{
                "name": "test/empty-arr",
                "execution_time": [],
            }],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_import_unknown_metric_rejected(self):
        """A typo'd metric name in test data raises ValueError."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "unk-metric-commit",
            "tests": [{
                "name": "test/unk-metric",
                "executin_time": 1.0,  # typo: should be execution_time
            }],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("executin_time", str(cm.exception))
        session.rollback()
        session.close()


class TestParameterKeyValidation(_ImportTestBase):
    """Tests for parameter key format validation and reserved name rejection."""

    def test_valid_key(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-valid-commit",
            "run_parameters": {"compiler_v2": "clang"},
            "tests": [{"name": "test/pk-valid", "execution_time": 1.0}],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()
        self.assertEqual(run.run_parameters, {"compiler_v2": "clang"})
        session.close()

    def test_invalid_key_with_dot(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-dot-commit",
            "run_parameters": {"param.key": "value"},
            "tests": [{"name": "test/pk-dot", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("param.key", str(cm.exception))
        session.rollback()
        session.close()

    def test_invalid_key_with_dash(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-dash-commit",
            "run_parameters": {"my-key": "value"},
            "tests": [{"name": "test/pk-dash", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("my-key", str(cm.exception))
        session.rollback()
        session.close()

    def test_reserved_name_commit(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-commit",
            "run_parameters": {"commit": "abc"},
            "tests": [{"name": "test/pk-reserved", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("reserved", str(cm.exception))
        session.rollback()
        session.close()

    def test_reserved_name_test(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-test",
            "run_parameters": {"test": "abc"},
            "tests": [{"name": "test/pk-res-test", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("reserved", str(cm.exception))
        session.rollback()
        session.close()

    def test_reserved_name_metric(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-metric",
            "run_parameters": {"metric": "abc"},
            "tests": [{"name": "test/pk-res-met", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError) as cm:
            self.tsdb.import_run(session, data)
        self.assertIn("reserved", str(cm.exception))
        session.rollback()
        session.close()

    def test_reserved_name_sort(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-sort",
            "run_parameters": {"sort": "abc"},
            "tests": [{"name": "test/pk-res-sort", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_reserved_name_cursor(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-cursor",
            "run_parameters": {"cursor": "abc"},
            "tests": [{"name": "test/pk-res-cursor", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_reserved_name_limit(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-limit",
            "run_parameters": {"limit": "abc"},
            "tests": [{"name": "test/pk-res-limit", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_reserved_name_run(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-run",
            "run_parameters": {"run": "abc"},
            "tests": [{"name": "test/pk-res-run", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_reserved_name_search(self):
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "pk-reserved-search",
            "run_parameters": {"search": "abc"},
            "tests": [{"name": "test/pk-res-search", "execution_time": 1.0}],
        }
        with self.assertRaises(ValueError):
            self.tsdb.import_run(session, data)
        session.rollback()
        session.close()

    def test_value_type_coercion_int(self):
        """Integer values should be coerced to strings."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "coerce-int-commit",
            "run_parameters": {"threads": 42},
            "tests": [{"name": "test/coerce-int", "execution_time": 1.0}],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()
        self.assertEqual(run.run_parameters["threads"], "42")
        session.close()

    def test_value_type_coercion_bool(self):
        """Boolean values should be coerced to strings."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "coerce-bool-commit",
            "run_parameters": {"debug": True},
            "tests": [{"name": "test/coerce-bool", "execution_time": 1.0}],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()
        self.assertEqual(run.run_parameters["debug"], "True")
        session.close()

    def test_value_type_coercion_float(self):
        """Float values should be coerced to strings."""
        session = self.Session()
        data = {
            "format_version": "5",
            "commit": "coerce-float-commit",
            "run_parameters": {"ratio": 3.14},
            "tests": [{"name": "test/coerce-float", "execution_time": 1.0}],
        }
        run = self.tsdb.import_run(session, data)
        session.commit()
        self.assertEqual(run.run_parameters["ratio"], "3.14")
        session.close()


class TestGetOrCreateTests(_ImportTestBase):

    def test_batch_creates_new_tests(self):
        """All names are new -- batch creates them all."""
        session = self.Session()
        names = ["batch/new-a", "batch/new-b", "batch/new-c"]
        result = self.tsdb.get_or_create_tests(session, names)
        session.commit()

        self.assertEqual(set(result.keys()), set(names))
        for name in names:
            self.assertIsInstance(result[name], int)
        for name in names:
            t = self.tsdb.get_test(session, name=name)
            self.assertIsNotNone(t)
            self.assertEqual(t.id, result[name])
        session.close()

    def test_batch_finds_existing_tests(self):
        """All names already exist -- batch finds them without inserting."""
        session = self.Session()
        pre = self.tsdb.get_or_create_tests(
            session, ["batch/existing-1", "batch/existing-2"])
        session.commit()

        result = self.tsdb.get_or_create_tests(
            session, ["batch/existing-1", "batch/existing-2"])
        self.assertEqual(result["batch/existing-1"], pre["batch/existing-1"])
        self.assertEqual(result["batch/existing-2"], pre["batch/existing-2"])
        session.close()

    def test_batch_mixed_existing_and_new(self):
        """Mix of existing and new names."""
        session = self.Session()
        pre = self.tsdb.get_or_create_tests(session, ["batch/mix-exist"])
        session.commit()

        result = self.tsdb.get_or_create_tests(
            session, ["batch/mix-exist", "batch/mix-new-1", "batch/mix-new-2"])
        session.commit()

        self.assertEqual(result["batch/mix-exist"], pre["batch/mix-exist"])
        self.assertIn("batch/mix-new-1", result)
        self.assertIn("batch/mix-new-2", result)
        self.assertIsNotNone(self.tsdb.get_test(session, name="batch/mix-new-1"))
        session.close()

    def test_batch_deduplicates_input(self):
        """Duplicate names in input are handled."""
        session = self.Session()
        result = self.tsdb.get_or_create_tests(
            session, ["batch/dup-a", "batch/dup-b", "batch/dup-a", "batch/dup-b", "batch/dup-a"])
        session.commit()

        self.assertEqual(len(result), 2)
        self.assertIn("batch/dup-a", result)
        self.assertIn("batch/dup-b", result)
        session.close()

    def test_batch_empty_input(self):
        """Empty input returns empty dict."""
        session = self.Session()
        result = self.tsdb.get_or_create_tests(session, [])
        self.assertEqual(result, {})
        session.close()

    def test_import_run_with_many_tests(self):
        """Submit a run with 100 tests."""
        session = self.Session()
        tests = [{"name": f"batch/many-{i}", "execution_time": float(i)}
                 for i in range(100)]
        data = {
            "format_version": "5",
            "commit": "batch-many-commit",
            "tests": tests,
        }
        run = self.tsdb.import_run(session, data)
        session.commit()

        samples = self.tsdb.list_samples(session, run_id=run.id, limit=200)
        self.assertEqual(len(samples), 100)
        session.close()


class TestGetOrCreateCommit(_ImportTestBase):

    def test_empty_commit_string_rejected(self):
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.get_or_create_commit(session, "")
        session.close()

    def test_long_commit_string(self):
        session = self.Session()
        long_str = "a" * 256
        c = self.tsdb.get_or_create_commit(session, long_str)
        session.commit()
        self.assertEqual(c.commit, long_str)
        session.close()

    def test_special_chars_in_commit(self):
        session = self.Session()
        for s in ["with/slash", "with%percent", "unicode-\u00e9\u00e8", "with spaces"]:
            c = self.tsdb.get_or_create_commit(session, s)
            session.commit()
            self.assertEqual(c.commit, s)
        session.close()


class TestSearchMethods(_ImportTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        session = cls.Session()
        # Seed some data
        cls.tsdb.get_or_create_commit(session, "abc-100", git_sha="sha-abc", author="Alice")
        cls.tsdb.get_or_create_commit(session, "abc-200", git_sha="sha-abd", author="Bob")
        cls.tsdb.get_or_create_commit(session, "xyz-100", git_sha="sha-xyz", author="Alice Smith")
        session.commit()
        session.close()

    def test_search_commits_by_commit_string(self):
        session = self.Session()
        results = self.tsdb.list_commits(session, search="abc")
        names = [c.commit for c in results]
        self.assertIn("abc-100", names)
        self.assertIn("abc-200", names)
        self.assertNotIn("xyz-100", names)
        session.close()

    def test_search_commits_by_substring(self):
        """Search matches a substring in the middle of a commit string."""
        session = self.Session()
        results = self.tsdb.list_commits(session, search="c-10")
        names = [c.commit for c in results]
        self.assertIn("abc-100", names)
        session.close()

    def test_search_commits_case_insensitive(self):
        """Search is case-insensitive for commit strings and fields."""
        session = self.Session()
        self.tsdb.get_or_create_commit(
            session, "MiXeD-CaSe-Commit", author="TestAuthor")
        session.commit()

        results = self.tsdb.list_commits(session, search="mixed-case-commit")
        names = [c.commit for c in results]
        self.assertIn("MiXeD-CaSe-Commit", names)

        results = self.tsdb.list_commits(session, search="MIXED-CASE-COMMIT")
        names = [c.commit for c in results]
        self.assertIn("MiXeD-CaSe-Commit", names)
        session.close()

    def test_search_commits_by_searchable_field(self):
        """Search should match across searchable commit_fields (author)."""
        session = self.Session()
        results = self.tsdb.list_commits(session, search="Alice")
        names = [c.commit for c in results]
        self.assertIn("abc-100", names)
        self.assertIn("xyz-100", names)
        session.close()

    def test_empty_search_returns_all(self):
        session = self.Session()
        all_commits = self.tsdb.list_commits(session)
        searched = self.tsdb.list_commits(session, search=None)
        self.assertEqual(len(all_commits), len(searched))
        session.close()

    def test_search_with_sql_special_percent(self):
        """Search with '%' should be treated literally, not as a wildcard."""
        session = self.Session()
        self.tsdb.get_or_create_commit(session, "100%done")
        self.tsdb.get_or_create_commit(session, "100_safe")
        session.commit()

        results = self.tsdb.list_commits(session, search="100%")
        names = [c.commit for c in results]
        self.assertIn("100%done", names)
        self.assertNotIn("100_safe", names)
        session.close()

    def test_default_limit_on_list_commits(self):
        session = self.Session()
        results = self.tsdb.list_commits(session, limit=2)
        self.assertLessEqual(len(results), 2)
        session.close()

    def test_default_limit_on_list_runs(self):
        session = self.Session()
        results = self.tsdb.list_runs(session, limit=1)
        self.assertLessEqual(len(results), 1)
        session.close()


class TestSchemaStorageInDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)
        create_global_tables(cls.engine)

    @classmethod
    def tearDownClass(cls):
        from lnt.server.db.v5.models import _global_base
        _global_base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def setUp(self):
        session = self.Session()
        session.query(V5Schema).delete()
        row = session.query(V5SchemaVersion).get(1)
        if row:
            row.version = 0
        else:
            session.add(V5SchemaVersion(id=1, version=0))
        session.commit()
        session.close()

    def _make_v5db(self):
        """Create a V5DB wired to the test Postgres database."""
        db_uri = os.environ.get('LNT_TEST_DB_URI')
        db_name = os.environ.get('LNT_TEST_DB_NAME')
        path = f"{db_uri}/{db_name}"

        class _FakeConfig:
            schemasDir = "/nonexistent"
        return V5DB(path, _FakeConfig())

    def test_create_suite(self):
        v5db = self._make_v5db()
        schema = parse_schema({
            "name": "create_test",
            "metrics": [{"name": "time", "type": "real"}],
        })
        session = v5db.make_session()
        tsdb = v5db.create_suite(session, schema)
        session.commit()
        session.close()

        self.assertIsNotNone(tsdb)
        self.assertEqual(tsdb.name, "create_test")
        self.assertIn("create_test", v5db.testsuite)

        # Verify row in v5_schema
        session = v5db.make_session()
        row = session.query(V5Schema).get("create_test")
        self.assertIsNotNone(row)
        data = json.loads(row.schema_json)
        self.assertEqual(data["name"], "create_test")
        # schema_json should NOT have machine_fields
        self.assertNotIn("machine_fields", data)
        session.close()

        # Clean up
        session = v5db.make_session()
        v5db.delete_suite(session, "create_test")
        session.commit()
        session.close()
        v5db.close()

    def test_get_suite(self):
        v5db = self._make_v5db()
        self.assertIsNone(v5db.get_suite("nonexistent"))

        schema = parse_schema({
            "name": "get_test",
            "metrics": [{"name": "time", "type": "real"}],
        })
        session = v5db.make_session()
        v5db.create_suite(session, schema)
        session.commit()
        session.close()

        result = v5db.get_suite("get_test")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "get_test")

        # Clean up
        session = v5db.make_session()
        v5db.delete_suite(session, "get_test")
        session.commit()
        session.close()
        v5db.close()

    def test_delete_suite(self):
        v5db = self._make_v5db()
        schema = parse_schema({
            "name": "del_test",
            "metrics": [{"name": "time", "type": "real"}],
        })
        session = v5db.make_session()
        v5db.create_suite(session, schema)
        session.commit()
        session.close()

        self.assertIn("del_test", v5db.testsuite)

        session = v5db.make_session()
        v5db.delete_suite(session, "del_test")
        session.commit()
        session.close()

        self.assertNotIn("del_test", v5db.testsuite)
        self.assertIsNone(v5db.get_suite("del_test"))
        v5db.close()

    def test_schema_version_bumped(self):
        """create_suite and delete_suite bump the version counter."""
        v5db = self._make_v5db()
        session = v5db.make_session()

        ver_before = session.query(V5SchemaVersion).get(1).version

        schema = parse_schema({
            "name": "ver_test",
            "metrics": [{"name": "time", "type": "real"}],
        })
        v5db.create_suite(session, schema)
        session.commit()

        ver_after_create = session.query(V5SchemaVersion).get(1).version
        self.assertEqual(ver_after_create, ver_before + 1)

        v5db.delete_suite(session, "ver_test")
        session.commit()

        ver_after_delete = session.query(V5SchemaVersion).get(1).version
        self.assertEqual(ver_after_delete, ver_before + 2)
        session.close()
        v5db.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
