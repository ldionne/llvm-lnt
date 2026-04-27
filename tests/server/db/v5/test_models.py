# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     python %s
# END.

import datetime
import os
import sys
import unittest

import sqlalchemy
import sqlalchemy.exc

from lnt.server.db.v5.schema import parse_schema
from lnt.server.db.v5.models import _global_base, create_suite_models, utcnow
from lnt.server.db.v5 import V5DB, initialize_v5_database


def _db_path():
    db_uri = os.environ.get('LNT_TEST_DB_URI')
    db_name = os.environ.get('LNT_TEST_DB_NAME')
    if not db_uri or not db_name:
        raise unittest.SkipTest(
            "LNT_TEST_DB_URI / LNT_TEST_DB_NAME not set "
            "(run via with_postgres.sh)")
    return f"{db_uri}/{db_name}"


def _make_engine():
    return sqlalchemy.create_engine(_db_path())


def _test_schema():
    return parse_schema({
        "name": "t",
        "metrics": [
            {"name": "compile_time", "type": "real"},
            {"name": "execution_time", "type": "real"},
            {"name": "compile_status", "type": "status"},
        ],
        "commit_fields": [
            {"name": "git_sha", "searchable": True},
            {"name": "author", "searchable": True},
            {"name": "message", "type": "text"},
        ],
    })


class TestModelCreation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.models = create_suite_models(cls.schema)
        cls.models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_commit_table_exists(self):
        insp = sqlalchemy.inspect(self.engine)
        self.assertIn("t_Commit", insp.get_table_names())

    def test_commit_has_dynamic_columns(self):
        """Dynamic commit_fields should appear as columns."""
        insp = sqlalchemy.inspect(self.engine)
        cols = {c['name'] for c in insp.get_columns("t_Commit")}
        self.assertIn("git_sha", cols)
        self.assertIn("author", cols)
        self.assertIn("message", cols)

    def test_no_machine_table(self):
        """Machine table should not exist -- it was removed."""
        insp = sqlalchemy.inspect(self.engine)
        self.assertNotIn("t_Machine", insp.get_table_names())

    def test_sample_table_has_metric_columns(self):
        """Schema-defined metrics should appear as dynamic columns."""
        insp = sqlalchemy.inspect(self.engine)
        cols = {c['name'] for c in insp.get_columns("t_Sample")}
        self.assertIn("compile_time", cols)
        self.assertIn("execution_time", cols)
        self.assertIn("compile_status", cols)

    def test_run_has_no_machine_id(self):
        """Run table should not have a machine_id column."""
        insp = sqlalchemy.inspect(self.engine)
        cols = {c['name'] for c in insp.get_columns("t_Run")}
        self.assertNotIn("machine_id", cols)
        self.assertIn("run_parameters", cols)

    def test_all_tables_created(self):
        """All per-suite tables should exist (no Machine)."""
        insp = sqlalchemy.inspect(self.engine)
        tables = set(insp.get_table_names())
        expected = {
            "t_Commit", "t_Run", "t_Test",
            "t_Sample", "t_Regression",
            "t_RegressionIndicator",
            "t_ParameterKey", "t_ParameterValue",
            "t_DashboardCard",
        }
        self.assertTrue(expected.issubset(tables), f"Missing: {expected - tables}")

    def test_utcnow_returns_utc_aware_datetime(self):
        """utcnow() must return a timezone-aware UTC datetime."""
        result = utcnow()
        self.assertEqual(result.tzinfo, datetime.timezone.utc)


class TestCommitCRUD(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.models = create_suite_models(cls.schema)
        cls.models.base.metadata.drop_all(cls.engine)
        cls.models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_create_commit(self):
        session = self.Session()
        c = self.models.Commit()
        c.commit = "abc123"
        c.git_sha = "abc123def456"
        c.author = "Jane"
        session.add(c)
        session.commit()
        self.assertIsNotNone(c.id)
        self.assertIsNone(c.ordinal)  # ordinal always NULL on creation
        session.close()

    def test_unique_commit_string(self):
        """Duplicate commit strings should raise IntegrityError."""
        session = self.Session()
        c1 = self.models.Commit()
        c1.commit = "unique_test_1"
        session.add(c1)
        session.commit()

        c2 = self.models.Commit()
        c2.commit = "unique_test_1"
        session.add(c2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()

    def test_ordinal_unique(self):
        session = self.Session()
        c1 = self.models.Commit()
        c1.commit = "ord_test_1"
        c1.ordinal = 42
        session.add(c1)
        session.commit()

        c2 = self.models.Commit()
        c2.commit = "ord_test_2"
        c2.ordinal = 42
        session.add(c2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()

    def test_ordinal_nullable(self):
        """Ordinal can be NULL (multiple commits with NULL ordinal OK)."""
        session = self.Session()
        for i in range(3):
            c = self.models.Commit()
            c.commit = f"null_ord_{i}"
            session.add(c)
        session.commit()

        nulls = (
            session.query(self.models.Commit)
            .filter(self.models.Commit.commit.like("null_ord_%"))
            .all()
        )
        self.assertEqual(len(nulls), 3)
        for c in nulls:
            self.assertIsNone(c.ordinal)
        session.close()


class _ModelTestBase(unittest.TestCase):
    """Shared setup/teardown and helpers for model-level tests."""

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.models = create_suite_models(cls.schema)
        cls.models.base.metadata.drop_all(cls.engine)
        cls.models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def _make_test(self, session, name):
        t = self.models.Test()
        t.name = name
        session.add(t)
        session.flush()
        return t

    def _make_commit(self, session, commit_str):
        c = self.models.Commit()
        c.commit = commit_str
        session.add(c)
        session.flush()
        return c

    def _make_run(self, session, commit):
        import uuid as uuid_module
        r = self.models.Run()
        r.uuid = str(uuid_module.uuid4())
        r.commit_id = commit.id
        r.submitted_at = utcnow()
        r.run_parameters = {}
        session.add(r)
        session.flush()
        return r


class TestRunCRUD(_ModelTestBase):

    def test_create_run_with_commit(self):
        session = self.Session()
        commit = self._make_commit(session, "run-c-1")
        run = self._make_run(session, commit)
        session.commit()
        self.assertIsNotNone(run.id)
        self.assertEqual(run.commit_id, commit.id)
        session.close()

    def test_create_run_without_commit_fails(self):
        """Creating a run with NULL commit_id should raise IntegrityError."""
        import uuid as uuid_module
        session = self.Session()
        run = self.models.Run()
        run.uuid = str(uuid_module.uuid4())
        run.commit_id = None
        run.submitted_at = utcnow()
        run.run_parameters = {}
        session.add(run)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()

    def test_uuid_unique(self):
        session = self.Session()
        commit = self._make_commit(session, "run-c-3")
        r1 = self.models.Run()
        r1.uuid = "cccccccc-1111-2222-3333-444444444444"
        r1.commit_id = commit.id
        r1.submitted_at = utcnow()
        r1.run_parameters = {}
        session.add(r1)
        session.commit()

        r2 = self.models.Run()
        r2.uuid = "cccccccc-1111-2222-3333-444444444444"  # same
        r2.commit_id = commit.id
        r2.submitted_at = utcnow()
        r2.run_parameters = {}
        session.add(r2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()

    def test_run_parameters_jsonb(self):
        session = self.Session()
        commit = self._make_commit(session, "run-c-4")
        import uuid as uuid_module
        run = self.models.Run()
        run.uuid = str(uuid_module.uuid4())
        run.commit_id = commit.id
        run.submitted_at = utcnow()
        run.run_parameters = {
            "compiler": "clang-21",
            "os": "linux",
        }
        session.add(run)
        session.commit()

        fetched = session.query(self.models.Run).filter_by(
            uuid=run.uuid).one()
        self.assertEqual(fetched.run_parameters["compiler"], "clang-21")
        self.assertEqual(fetched.run_parameters["os"], "linux")
        session.close()


class TestSampleCreation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.models = create_suite_models(cls.schema)
        cls.models.base.metadata.drop_all(cls.engine)
        cls.models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_create_sample_with_metrics(self):
        import uuid as uuid_module
        session = self.Session()

        c = self.models.Commit()
        c.commit = "sample-commit"
        session.add(c)
        session.flush()

        t = self.models.Test()
        t.name = "test.suite/benchmark"
        session.add(t)
        session.flush()

        r = self.models.Run()
        r.uuid = str(uuid_module.uuid4())
        r.commit_id = c.id
        r.submitted_at = utcnow()
        r.run_parameters = {}
        session.add(r)
        session.flush()

        s = self.models.Sample()
        s.run_id = r.id
        s.test_id = t.id
        s.compile_time = 1.5
        s.execution_time = 0.3
        s.compile_status = 0
        session.add(s)
        session.commit()

        fetched = session.query(self.models.Sample).filter_by(run_id=r.id).one()
        self.assertAlmostEqual(fetched.compile_time, 1.5)
        self.assertAlmostEqual(fetched.execution_time, 0.3)
        self.assertEqual(fetched.compile_status, 0)
        session.close()

    def test_test_name_unique(self):
        session = self.Session()
        t1 = self.models.Test()
        t1.name = "unique-test"
        session.add(t1)
        session.commit()

        t2 = self.models.Test()
        t2.name = "unique-test"
        session.add(t2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()


class TestRegressionAndIndicatorModels(_ModelTestBase):

    def test_create_regression_with_commit_and_notes(self):
        """Create a Regression with commit_id (NOT NULL) and notes."""
        session = self.Session()
        c = self._make_commit(session, "reg-model-c1")

        reg = self.models.Regression()
        reg.uuid = "reg-model-uuid-00000000000000000"[:36]
        reg.title = "Test Regression"
        reg.bug = "BUG-1"
        reg.notes = "Some notes about the regression"
        reg.state = 0
        reg.commit_id = c.id
        session.add(reg)
        session.commit()

        self.assertIsNotNone(reg.id)
        self.assertEqual(reg.notes, "Some notes about the regression")
        self.assertEqual(reg.commit_id, c.id)
        session.close()

    def test_regression_commit_id_not_nullable(self):
        """Regression without a commit_id should fail (NOT NULL)."""
        session = self.Session()
        reg = self.models.Regression()
        reg.uuid = "reg-model-uuid-null-commit0000000"[:36]
        reg.title = "No commit regression"
        reg.state = 0
        session.add(reg)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()

    def test_regression_indicator_has_uuid(self):
        """RegressionIndicator should have a uuid field."""
        session = self.Session()
        c = self._make_commit(session, "ri-model-c1")
        run = self._make_run(session, c)
        t = self._make_test(session, "ri-model-t1")

        reg = self.models.Regression()
        reg.uuid = "reg-model-uuid-ri-uuid0000000000"[:36]
        reg.title = "RI UUID test"
        reg.state = 0
        reg.commit_id = c.id
        session.add(reg)
        session.flush()

        ri = self.models.RegressionIndicator()
        ri.uuid = "ri-model-uuid-000000000000000000"[:36]
        ri.regression_id = reg.id
        ri.run_id = run.id
        ri.test_id = t.id
        ri.metric = "execution_time"
        session.add(ri)
        session.commit()

        self.assertIsNotNone(ri.id)
        self.assertEqual(ri.uuid, "ri-model-uuid-000000000000000000"[:36])
        session.close()

    def test_regression_indicator_unique_constraint(self):
        """Duplicate (regression_id, run_id, test_id, metric) should fail."""
        session = self.Session()
        c = self._make_commit(session, "ri-model-c-uniq")
        run = self._make_run(session, c)
        t = self._make_test(session, "ri-model-t-uniq")

        reg = self.models.Regression()
        reg.uuid = "reg-model-uuid-uniq00000000000000"[:36]
        reg.title = "Unique constraint test"
        reg.state = 0
        reg.commit_id = c.id
        session.add(reg)
        session.flush()

        import uuid as uuid_module
        ri1 = self.models.RegressionIndicator()
        ri1.uuid = str(uuid_module.uuid4())
        ri1.regression_id = reg.id
        ri1.run_id = run.id
        ri1.test_id = t.id
        ri1.metric = "execution_time"
        session.add(ri1)
        session.flush()

        ri2 = self.models.RegressionIndicator()
        ri2.uuid = str(uuid_module.uuid4())
        ri2.regression_id = reg.id
        ri2.run_id = run.id
        ri2.test_id = t.id
        ri2.metric = "execution_time"
        session.add(ri2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.flush()
        session.rollback()
        session.close()

    def test_same_triple_on_different_regressions_ok(self):
        """Same (run, test, metric) on different regressions should succeed."""
        session = self.Session()
        c = self._make_commit(session, "ri-model-c-multi")
        run = self._make_run(session, c)
        t = self._make_test(session, "ri-model-t-multi")

        import uuid as uuid_module
        reg1 = self.models.Regression()
        reg1.uuid = str(uuid_module.uuid4())
        reg1.title = "Reg 1"
        reg1.state = 0
        reg1.commit_id = c.id
        session.add(reg1)

        reg2 = self.models.Regression()
        reg2.uuid = str(uuid_module.uuid4())
        reg2.title = "Reg 2"
        reg2.state = 0
        reg2.commit_id = c.id
        session.add(reg2)
        session.flush()

        ri1 = self.models.RegressionIndicator()
        ri1.uuid = str(uuid_module.uuid4())
        ri1.regression_id = reg1.id
        ri1.run_id = run.id
        ri1.test_id = t.id
        ri1.metric = "execution_time"
        session.add(ri1)

        ri2 = self.models.RegressionIndicator()
        ri2.uuid = str(uuid_module.uuid4())
        ri2.regression_id = reg2.id
        ri2.run_id = run.id
        ri2.test_id = t.id
        ri2.metric = "execution_time"
        session.add(ri2)
        session.commit()

        self.assertIsNotNone(ri1.id)
        self.assertIsNotNone(ri2.id)
        session.close()

    def test_delete_regression_cascades_to_indicators(self):
        """Deleting a Regression should cascade-delete its indicators."""
        session = self.Session()
        c = self._make_commit(session, "ri-model-c-cascade")
        run = self._make_run(session, c)
        t = self._make_test(session, "ri-model-t-cascade")

        import uuid as uuid_module
        reg = self.models.Regression()
        reg.uuid = str(uuid_module.uuid4())
        reg.title = "Cascade test"
        reg.state = 0
        reg.commit_id = c.id
        session.add(reg)
        session.flush()

        ri = self.models.RegressionIndicator()
        ri.uuid = str(uuid_module.uuid4())
        ri.regression_id = reg.id
        ri.run_id = run.id
        ri.test_id = t.id
        ri.metric = "execution_time"
        session.add(ri)
        session.flush()

        reg_id = reg.id
        session.delete(reg)
        session.commit()

        remaining = (
            session.query(self.models.RegressionIndicator)
            .filter_by(regression_id=reg_id)
            .all()
        )
        self.assertEqual(len(remaining), 0)
        session.close()

    def test_commit_referenced_by_regression_cannot_be_deleted(self):
        """Deleting a Commit referenced by Regression.commit_id should fail."""
        session = self.Session()
        c = self._make_commit(session, "reg-model-c-fk")

        import uuid as uuid_module
        reg = self.models.Regression()
        reg.uuid = str(uuid_module.uuid4())
        reg.title = "FK test"
        reg.state = 0
        reg.commit_id = c.id
        session.add(reg)
        session.commit()

        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.delete(c)
            session.flush()
        session.rollback()
        session.close()


class TestParameterKeyModel(_ModelTestBase):
    """Tests for the ParameterKey model."""

    def test_create_parameter_key(self):
        session = self.Session()
        pk = self.models.ParameterKey()
        pk.key = "compiler"
        session.add(pk)
        session.commit()
        self.assertIsNotNone(pk.id)
        session.close()

    def test_key_unique(self):
        session = self.Session()
        pk1 = self.models.ParameterKey()
        pk1.key = "unique_key_test"
        session.add(pk1)
        session.commit()

        pk2 = self.models.ParameterKey()
        pk2.key = "unique_key_test"
        session.add(pk2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()


class TestParameterValueModel(_ModelTestBase):
    """Tests for the ParameterValue model."""

    def test_create_parameter_value(self):
        session = self.Session()
        pk = self.models.ParameterKey()
        pk.key = "pv_test_key"
        session.add(pk)
        session.flush()

        pv = self.models.ParameterValue()
        pv.key_id = pk.id
        pv.value = "clang-21"
        session.add(pv)
        session.commit()
        self.assertIsNotNone(pv.id)
        session.close()

    def test_key_value_unique(self):
        session = self.Session()
        pk = self.models.ParameterKey()
        pk.key = "pv_unique_key"
        session.add(pk)
        session.flush()

        pv1 = self.models.ParameterValue()
        pv1.key_id = pk.id
        pv1.value = "same_value"
        session.add(pv1)
        session.commit()

        pv2 = self.models.ParameterValue()
        pv2.key_id = pk.id
        pv2.value = "same_value"
        session.add(pv2)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            session.commit()
        session.rollback()
        session.close()


class TestDashboardCardModel(_ModelTestBase):
    """Tests for the DashboardCard model."""

    def test_create_dashboard_card(self):
        session = self.Session()
        card = self.models.DashboardCard()
        card.position = 0
        card.params = {"compiler": "clang-21"}
        card.metric = "execution_time"
        card.last_n = 500
        session.add(card)
        session.commit()
        self.assertIsNotNone(card.id)
        session.close()


class TestCascadingDeletes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()
        cls.schema = _test_schema()
        cls.models = create_suite_models(cls.schema)
        cls.models.base.metadata.drop_all(cls.engine)
        cls.models.base.metadata.create_all(cls.engine)
        cls.Session = sqlalchemy.orm.sessionmaker(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_delete_run_keeps_commit(self):
        """Deleting a run should NOT delete the commit."""
        import uuid as uuid_module
        session = self.Session()

        c = self.models.Commit()
        c.commit = "cascade-commit"
        session.add(c)
        session.flush()

        r = self.models.Run()
        r.uuid = str(uuid_module.uuid4())
        r.commit_id = c.id
        r.submitted_at = utcnow()
        r.run_parameters = {}
        session.add(r)
        session.flush()

        run_id = r.id
        commit_id = c.id

        session.delete(r)
        session.commit()

        # Run is gone
        self.assertIsNone(
            session.query(self.models.Run).get(run_id))
        # Commit survives
        self.assertIsNotNone(
            session.query(self.models.Commit).get(commit_id))
        session.close()


class TestInitializeV5Database(unittest.TestCase):
    """Tests for initialize_v5_database and V5DB read-only init."""

    @classmethod
    def setUpClass(cls):
        cls.engine = _make_engine()

    @classmethod
    def tearDownClass(cls):
        _global_base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def setUp(self):
        _global_base.metadata.drop_all(self.engine)

    def test_initialize_v5_database_idempotent(self):
        """Calling initialize_v5_database twice should not raise."""
        initialize_v5_database(_db_path())
        initialize_v5_database(_db_path())

        insp = sqlalchemy.inspect(self.engine)
        tables = insp.get_table_names()
        self.assertIn("v5_schema", tables)
        self.assertIn("v5_schema_version", tables)
        self.assertIn("api_key", tables)

    def test_v5db_init_on_initialized_database(self):
        """V5DB.__init__ should succeed read-only on an initialized DB."""
        initialize_v5_database(_db_path())

        class _FakeConfig:
            schemasDir = "/nonexistent"

        db = V5DB(_db_path(), _FakeConfig())
        self.assertEqual(db.testsuite, {})
        self.assertEqual(db._schema_version, 0)
        db.engine.dispose()

    def test_v5db_init_without_initialization_gives_clear_error(self):
        """V5DB.__init__ on an uninitialized DB should raise RuntimeError."""

        class _FakeConfig:
            schemasDir = "/nonexistent"

        with self.assertRaises(RuntimeError) as ctx:
            V5DB(_db_path(), _FakeConfig())
        self.assertIn("not initialized", str(ctx.exception))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
