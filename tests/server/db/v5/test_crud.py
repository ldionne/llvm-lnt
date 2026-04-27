# RUN: rm -rf %t.instance %t.pg.log
# RUN: %{utils}/with_postgres.sh %t.pg.log \
# RUN:     python %s
# END.

import os
import sys
import unittest

import sqlalchemy
import sqlalchemy.exc
import sqlalchemy.orm

from lnt.server.db.v5.schema import parse_schema
from lnt.server.db.v5.models import create_suite_models
from lnt.server.db.v5 import V5TestSuiteDB, VALID_REGRESSION_STATES


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


class _CRUDTestBase(unittest.TestCase):
    """Shared setup for CRUD method tests."""

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

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()


class TestUpdateCommit(unittest.TestCase):

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

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_set_ordinal(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uc-ord-1")
        self.assertIsNone(c.ordinal)

        self.tsdb.update_commit(session, c, ordinal=100)
        session.commit()

        fetched = self.tsdb.get_commit(session, commit="uc-ord-1")
        self.assertEqual(fetched.ordinal, 100)
        session.close()

    def test_clear_ordinal(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uc-ord-2")
        self.tsdb.update_commit(session, c, ordinal=200)
        session.commit()
        self.assertEqual(c.ordinal, 200)

        self.tsdb.update_commit(session, c, clear_ordinal=True)
        session.commit()

        fetched = self.tsdb.get_commit(session, commit="uc-ord-2")
        self.assertIsNone(fetched.ordinal)
        session.close()

    def test_set_metadata(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uc-meta-1")
        self.assertIsNone(c.author)

        self.tsdb.update_commit(session, c, author="Alice")
        session.commit()

        fetched = self.tsdb.get_commit(session, commit="uc-meta-1")
        self.assertEqual(fetched.author, "Alice")
        session.close()

    def test_overwrite_metadata(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uc-meta-2", author="Bob")
        session.commit()
        self.assertEqual(c.author, "Bob")

        self.tsdb.update_commit(session, c, author="Charlie")
        session.commit()

        fetched = self.tsdb.get_commit(session, commit="uc-meta-2")
        self.assertEqual(fetched.author, "Charlie")
        session.close()

    def test_set_ordinal_and_metadata_together(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uc-both-1")
        self.tsdb.update_commit(session, c, ordinal=300, author="Dave")
        session.commit()

        fetched = self.tsdb.get_commit(session, commit="uc-both-1")
        self.assertEqual(fetched.ordinal, 300)
        self.assertEqual(fetched.author, "Dave")
        session.close()


class TestRegressionCRUD(_CRUDTestBase):

    def test_create_regression_with_indicators(self):
        """Create a regression with run/test/metric indicators."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "reg-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["reg-test"])["reg-test"]
        session.flush()

        reg = self.tsdb.create_regression(
            session, "Perf regression",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            bug="BUG-123", commit=commit, state=0)
        session.commit()

        self.assertIsNotNone(reg.uuid)
        self.assertEqual(reg.title, "Perf regression")
        self.assertEqual(reg.bug, "BUG-123")
        self.assertEqual(reg.state, 0)
        self.assertIsNone(reg.notes)
        self.assertEqual(reg.commit_id, commit.id)

        indicators = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg.id)
            .all()
        )
        self.assertEqual(len(indicators), 1)
        self.assertEqual(indicators[0].run_id, run.id)
        self.assertEqual(indicators[0].test_id, test_id)
        self.assertEqual(indicators[0].metric, "execution_time")
        self.assertIsNotNone(indicators[0].uuid)
        session.close()

    def test_create_regression_requires_commit(self):
        """commit is required for regression creation."""
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.create_regression(
                session, "No commit", [], commit=None, state=0)
        session.close()

    def test_create_regression_with_notes_and_commit(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "reg-commit-1")
        session.flush()

        reg = self.tsdb.create_regression(
            session, "Noted regression", [],
            notes="Caused by vectorizer change",
            commit=commit,
            state=1)
        session.commit()

        self.assertEqual(reg.notes, "Caused by vectorizer change")
        self.assertEqual(reg.commit_id, commit.id)
        session.close()

    def test_create_regression_with_empty_indicators(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "reg-empty-c")
        reg = self.tsdb.create_regression(
            session, "Empty regression", [], commit=commit, state=0)
        session.commit()
        self.assertIsNotNone(reg.id)
        indicators = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg.id)
            .all()
        )
        self.assertEqual(len(indicators), 0)
        session.close()

    def test_update_regression_notes(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "upd-reg-notes-c")
        reg = self.tsdb.create_regression(
            session, "title", [], commit=commit, state=0)
        session.commit()

        self.tsdb.update_regression(
            session, reg, notes="New notes")
        session.commit()

        fetched = self.tsdb.get_regression(session, id=reg.id)
        self.assertEqual(fetched.notes, "New notes")
        session.close()

    def test_update_regression_commit(self):
        session = self.Session()
        commit1 = self.tsdb.get_or_create_commit(session, "upd-reg-c1")
        commit2 = self.tsdb.get_or_create_commit(session, "upd-reg-c2")
        reg = self.tsdb.create_regression(
            session, "title", [], commit=commit1, state=0)
        session.commit()

        self.tsdb.update_regression(
            session, reg, commit=commit2)
        session.commit()
        self.assertEqual(reg.commit_id, commit2.id)

        # Cannot clear commit to None
        with self.assertRaises(ValueError):
            self.tsdb.update_regression(
                session, reg, commit=None)
        session.close()

    def test_update_regression_commit_revalidates_indicators(self):
        """Changing commit must re-validate existing indicators."""
        session = self.Session()
        commit1 = self.tsdb.get_or_create_commit(session, "reval-c1")
        commit2 = self.tsdb.get_or_create_commit(session, "reval-c2")
        run1 = self.tsdb.create_run(session, commit=commit1)
        test_id = self.tsdb.get_or_create_tests(session, ["reval-test"])["reval-test"]
        session.flush()

        reg = self.tsdb.create_regression(
            session, "reval",
            [{"run_id": run1.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit1, state=0)
        session.commit()

        # Changing to commit2 should fail because indicator run is at commit1.
        with self.assertRaises(ValueError) as cm:
            self.tsdb.update_regression(session, reg, commit=commit2)
        self.assertIn("different commit", str(cm.exception))
        session.close()

    def test_update_regression_state_and_title(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "upd-reg-state-c")
        reg = self.tsdb.create_regression(
            session, "original", [], commit=commit, state=0)
        session.commit()

        self.tsdb.update_regression(
            session, reg, title="Updated", state=1)
        session.commit()

        fetched = self.tsdb.get_regression(session, uuid=reg.uuid)
        self.assertEqual(fetched.title, "Updated")
        self.assertEqual(fetched.state, 1)
        session.close()

    def test_delete_regression_cascades_to_indicators(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-reg-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["del-reg-test"])["del-reg-test"]
        session.flush()

        reg = self.tsdb.create_regression(
            session, "to delete",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.commit()
        reg_id = reg.id

        self.tsdb.delete_regression(session, reg_id)
        session.commit()

        self.assertIsNone(self.tsdb.get_regression(session, id=reg_id))
        indicators = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg_id)
            .all()
        )
        self.assertEqual(len(indicators), 0)
        session.close()

    def test_list_regressions_by_state(self):
        session = self.Session()
        c1 = self.tsdb.get_or_create_commit(session, "list-reg-c1")
        c2 = self.tsdb.get_or_create_commit(session, "list-reg-c2")
        self.tsdb.create_regression(
            session, "active-one", [], commit=c1, state=1)
        self.tsdb.create_regression(
            session, "detected-one", [], commit=c2, state=0)
        session.commit()

        active = self.tsdb.list_regressions(session, state=1)
        self.assertGreater(len(active), 0)
        self.assertTrue(
            all(r.state == 1 for r in active))
        session.close()

    def test_update_regression_clear_nullable_fields(self):
        """Verify _UNSET pattern allows clearing nullable fields to None."""
        cases = [
            ("notes", "some notes"),
            ("bug", "BUG-1"),
        ]
        for field, initial in cases:
            with self.subTest(field=field):
                session = self.Session()
                commit = self.tsdb.get_or_create_commit(
                    session, f"clear-{field}-c")
                reg = self.tsdb.create_regression(
                    session, "title", [], **{field: initial},
                    commit=commit, state=0)
                session.commit()
                self.assertEqual(getattr(reg, field), initial)

                self.tsdb.update_regression(session, reg, **{field: None})
                session.commit()
                self.assertIsNone(getattr(reg, field))
                session.close()

    def test_update_regression_clear_title(self):
        """Verify _UNSET pattern allows clearing title to None."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "clear-title-c")
        reg = self.tsdb.create_regression(
            session, "a title", [], commit=commit, state=0)
        session.commit()

        self.tsdb.update_regression(session, reg, title=None)
        session.commit()
        self.assertIsNone(reg.title)
        session.close()

    def test_old_state_values_rejected(self):
        """States 5 and 6 (old staged/detected_fixed) must be rejected."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "old-state-c")
        with self.assertRaises(ValueError):
            self.tsdb.create_regression(
                session, "old state", [], commit=commit, state=5)
        with self.assertRaises(ValueError):
            self.tsdb.create_regression(
                session, "old state", [], commit=commit, state=6)
        session.close()


class TestDeleteCommit(unittest.TestCase):
    """Deletion cascades to runs/samples but is blocked by Regressions."""

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

    @classmethod
    def tearDownClass(cls):
        cls.suite_models.base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_delete_commit_cascades_to_runs_and_samples(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-commit-c1")
        test_id = self.tsdb.get_or_create_tests(session, ["del-commit-test"])["del-commit-test"]
        run = self.tsdb.create_run(session, commit=commit)
        self.tsdb.create_samples(session, run, [{
            "test_id": test_id,
            "execution_time": 1.0,
        }])
        session.flush()

        commit_id = commit.id
        run_id = run.id

        self.tsdb.delete_commit(session, commit_id)
        session.commit()

        # Commit, run, and samples are gone
        self.assertIsNone(
            session.query(self.tsdb.Commit).get(commit_id))
        self.assertIsNone(
            session.query(self.tsdb.Run).get(run_id))
        samples = (
            session.query(self.tsdb.Sample)
            .filter_by(run_id=run_id)
            .all()
        )
        self.assertEqual(len(samples), 0)
        session.close()

    def test_delete_commit_blocked_by_regression_commit_ref(self):
        """Cannot delete a commit referenced by a Regression's commit_id."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-commit-reg-c")
        session.flush()

        self.tsdb.create_regression(
            session, "blocking reg", [],
            commit=commit, state=0)
        session.flush()

        with self.assertRaises(ValueError):
            self.tsdb.delete_commit(session, commit.id)

        session.close()

    def test_delete_commit_blocked_by_indicator_runs(self):
        """Cannot delete a commit when indicators reference runs at that commit."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-commit-ind-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["del-commit-ind-test"])["del-commit-ind-test"]
        session.flush()

        # Create regression at this commit with indicator referencing the run.
        reg = self.tsdb.create_regression(
            session, "ind-blocking",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.flush()

        # Create a different commit to test that indicator blocks deletion
        # of the original commit (the regression references commit via commit_id,
        # but we also check indicators via runs).
        # Actually, this commit is also blocked by Regression.commit_id, so
        # let's make a more precise test: create a regression at a DIFFERENT
        # commit but with indicators referencing runs at our commit.
        commit2 = self.tsdb.get_or_create_commit(session, "del-commit-ind-c2")
        run2 = self.tsdb.create_run(session, commit=commit)  # run at original commit
        session.flush()

        reg2 = self.tsdb.create_regression(
            session, "ind-blocking-2",
            [{"run_id": run2.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.flush()

        with self.assertRaises(ValueError):
            self.tsdb.delete_commit(session, commit.id)
        session.close()

    def test_delete_nonexistent_commit(self):
        session = self.Session()
        self.tsdb.delete_commit(session, 999999)
        session.close()


class TestDeleteRun(_CRUDTestBase):
    """Tests for delete_run with indicator guard."""

    def test_delete_run_without_indicators(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-run-c1")
        run = self.tsdb.create_run(session, commit=commit)
        session.flush()
        run_id = run.id

        self.tsdb.delete_run(session, run_id)
        session.commit()

        self.assertIsNone(
            session.query(self.tsdb.Run).get(run_id))
        session.close()

    def test_delete_run_blocked_by_indicators(self):
        """Cannot delete a run referenced by RegressionIndicators."""
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "del-run-ind-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["del-run-ind-test"])["del-run-ind-test"]
        session.flush()

        self.tsdb.create_regression(
            session, "blocking",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.flush()

        with self.assertRaises(ValueError) as cm:
            self.tsdb.delete_run(session, run.id)
        self.assertIn("RegressionIndicator", str(cm.exception))
        session.close()

    def test_delete_nonexistent_run(self):
        session = self.Session()
        self.tsdb.delete_run(session, 999999)
        session.close()


class TestGetTest(_CRUDTestBase):

    def test_get_test_by_name(self):
        session = self.Session()
        self.tsdb.get_or_create_tests(session, ["get-test-1"])
        session.commit()

        fetched = self.tsdb.get_test(session, name="get-test-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "get-test-1")
        session.close()

    def test_get_test_by_id(self):
        session = self.Session()
        t_id = self.tsdb.get_or_create_tests(session, ["get-test-2"])["get-test-2"]
        session.commit()

        fetched = self.tsdb.get_test(session, id=t_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "get-test-2")
        session.close()

    def test_get_test_not_found(self):
        session = self.Session()
        self.assertIsNone(self.tsdb.get_test(session, name="nonexistent"))
        session.close()

    def test_get_test_no_args_raises(self):
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.get_test(session)
        session.close()


class TestListSamples(_CRUDTestBase):

    def test_list_samples_by_run(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ls-c")
        test_id = self.tsdb.get_or_create_tests(session, ["ls-test"])["ls-test"]
        run = self.tsdb.create_run(session, commit=commit)
        self.tsdb.create_samples(session, run, [
            {"test_id": test_id, "execution_time": 1.0},
        ])
        session.commit()

        results = self.tsdb.list_samples(session, run_id=run.id)
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].execution_time, 1.0)
        session.close()

    def test_list_samples_by_test(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ls-c2")
        _ids = self.tsdb.get_or_create_tests(session, ["ls-test-a", "ls-test-b"])
        test_a_id = _ids["ls-test-a"]
        test_b_id = _ids["ls-test-b"]
        run = self.tsdb.create_run(session, commit=commit)
        self.tsdb.create_samples(session, run, [
            {"test_id": test_a_id, "execution_time": 1.0},
            {"test_id": test_b_id, "execution_time": 2.0},
        ])
        session.commit()

        results = self.tsdb.list_samples(session, test_id=test_a_id)
        test_ids = [s.test_id for s in results]
        self.assertTrue(all(tid == test_a_id for tid in test_ids))
        session.close()

    def test_list_samples_empty(self):
        session = self.Session()
        results = self.tsdb.list_samples(session, run_id=999999)
        self.assertEqual(len(results), 0)
        session.close()


class TestRegressionIndicatorManagement(_CRUDTestBase):

    def test_add_regression_indicator(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-add-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-add-test"])["ri-add-test"]
        reg = self.tsdb.create_regression(
            session, "add-ind", [], commit=commit, state=0)
        session.flush()

        ri = self.tsdb.add_regression_indicator(
            session, reg, run.id, test_id, "execution_time")
        session.commit()

        self.assertIsNotNone(ri.id)
        self.assertIsNotNone(ri.uuid)
        self.assertEqual(ri.run_id, run.id)
        self.assertEqual(ri.test_id, test_id)
        self.assertEqual(ri.metric, "execution_time")
        session.close()

    def test_add_indicator_wrong_commit_rejected(self):
        """Indicator run must share the regression's commit."""
        session = self.Session()
        commit1 = self.tsdb.get_or_create_commit(session, "ri-wrong-c1")
        commit2 = self.tsdb.get_or_create_commit(session, "ri-wrong-c2")
        run = self.tsdb.create_run(session, commit=commit2)  # different commit
        test_id = self.tsdb.get_or_create_tests(session, ["ri-wrong-test"])["ri-wrong-test"]
        reg = self.tsdb.create_regression(
            session, "wrong-commit", [], commit=commit1, state=0)
        session.flush()

        with self.assertRaises(ValueError) as cm:
            self.tsdb.add_regression_indicator(
                session, reg, run.id, test_id, "execution_time")
        self.assertIn("commit", str(cm.exception))
        session.close()

    def test_add_duplicate_indicator_rejected(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-dup-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-dup-test"])["ri-dup-test"]
        reg = self.tsdb.create_regression(
            session, "dup-ind",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.commit()

        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            self.tsdb.add_regression_indicator(
                session, reg, run.id, test_id, "execution_time")
        session.rollback()
        session.close()

    def test_same_triple_on_different_regressions_ok(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-multi-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-multi-test"])["ri-multi-test"]
        reg1 = self.tsdb.create_regression(
            session, "reg1", [], commit=commit, state=0)
        reg2 = self.tsdb.create_regression(
            session, "reg2", [], commit=commit, state=0)
        session.flush()

        ri1 = self.tsdb.add_regression_indicator(
            session, reg1, run.id, test_id, "execution_time")
        ri2 = self.tsdb.add_regression_indicator(
            session, reg2, run.id, test_id, "execution_time")
        session.commit()

        self.assertIsNotNone(ri1.id)
        self.assertIsNotNone(ri2.id)
        session.close()

    def test_remove_regression_indicator_by_uuid(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-rem-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-rem-test"])["ri-rem-test"]
        reg = self.tsdb.create_regression(
            session, "rem-ind",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.commit()

        indicator = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg.id)
            .first()
        )
        removed = self.tsdb.remove_regression_indicator(
            session, reg.id, indicator.uuid)
        session.commit()
        self.assertTrue(removed)

        remaining = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg.id)
            .all()
        )
        self.assertEqual(len(remaining), 0)
        session.close()

    def test_remove_nonexistent_indicator(self):
        session = self.Session()
        removed = self.tsdb.remove_regression_indicator(
            session, 999, "nonexistent-uuid")
        self.assertFalse(removed)
        session.close()

    def test_get_regression_indicator_by_uuid(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-get-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-get-test"])["ri-get-test"]
        reg = self.tsdb.create_regression(
            session, "get-ind",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.commit()

        indicator = (
            session.query(self.tsdb.RegressionIndicator)
            .filter_by(regression_id=reg.id)
            .first()
        )
        fetched = self.tsdb.get_regression_indicator(
            session, uuid=indicator.uuid)
        self.assertEqual(fetched.id, indicator.id)
        session.close()

    def test_get_regression_indicator_requires_id_or_uuid(self):
        session = self.Session()
        with self.assertRaises(ValueError):
            self.tsdb.get_regression_indicator(session)
        session.close()

    def test_batch_add_indicators_silently_ignores_duplicates(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "ri-batch-c")
        run = self.tsdb.create_run(session, commit=commit)
        test_id = self.tsdb.get_or_create_tests(session, ["ri-batch-test"])["ri-batch-test"]
        reg = self.tsdb.create_regression(
            session, "batch",
            [{"run_id": run.id, "test_id": test_id,
              "metric": "execution_time"}],
            commit=commit, state=0)
        session.commit()

        test2_id = self.tsdb.get_or_create_tests(session, ["ri-batch-test2"])["ri-batch-test2"]
        session.flush()
        created = self.tsdb.add_regression_indicators_batch(
            session, reg,
            [
                {"run_id": run.id, "test_id": test_id,
                 "metric": "execution_time"},  # duplicate
                {"run_id": run.id, "test_id": test2_id,
                 "metric": "execution_time"},  # new
            ])
        session.commit()

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].test_id, test2_id)
        session.close()


class TestRegressionStateValidation(_CRUDTestBase):

    def test_create_with_invalid_state(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "inv-state-c")
        with self.assertRaises(ValueError):
            self.tsdb.create_regression(
                session, "bad state", [], commit=commit, state=99)
        session.close()

    def test_update_with_invalid_state(self):
        session = self.Session()
        commit = self.tsdb.get_or_create_commit(session, "upd-inv-state-c")
        reg = self.tsdb.create_regression(
            session, "valid", [], commit=commit, state=0)
        session.commit()

        with self.assertRaises(ValueError):
            self.tsdb.update_regression(session, reg, state=-1)
        session.close()

    def test_all_valid_states_accepted(self):
        session = self.Session()
        for state_val in sorted(VALID_REGRESSION_STATES):
            commit = self.tsdb.get_or_create_commit(
                session, f"state-{state_val}-c")
            reg = self.tsdb.create_regression(
                session, f"state-{state_val}", [],
                commit=commit, state=state_val)
            self.assertEqual(reg.state, state_val)
        session.commit()
        session.close()


class TestUnknownFieldRejection(_CRUDTestBase):
    """Unknown field/metric names must raise ValueError."""

    def test_get_or_create_commit_unknown_field(self):
        session = self.Session()
        with self.assertRaises(ValueError) as cm:
            self.tsdb.get_or_create_commit(
                session, "bad-commit", bogus_field="x")
        self.assertIn("bogus_field", str(cm.exception))
        session.close()

    def test_update_commit_unknown_field(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uf-commit")
        with self.assertRaises(ValueError) as cm:
            self.tsdb.update_commit(session, c, nonexistent="x")
        self.assertIn("nonexistent", str(cm.exception))
        session.close()

    def test_create_samples_unknown_metric(self):
        session = self.Session()
        c = self.tsdb.get_or_create_commit(session, "uf-sample-c")
        run = self.tsdb.create_run(session, commit=c)
        t_id = self.tsdb.get_or_create_tests(session, ["uf-test"])["uf-test"]
        with self.assertRaises(ValueError) as cm:
            self.tsdb.create_samples(session, run, [
                {"test_id": t_id, "executin_time": 1.0},
            ])
        self.assertIn("executin_time", str(cm.exception))
        session.close()


class TestParameterKeyValueUpsert(_CRUDTestBase):
    """Tests for parameter key/value upsert during import."""

    def test_upsert_creates_keys_and_values(self):
        session = self.Session()
        self.tsdb._upsert_parameter_keys_and_values(session, {
            "compiler": "clang-21",
            "os": "linux",
        })
        session.commit()

        keys = self.tsdb.list_parameter_keys(session)
        key_names = [k.key for k in keys]
        self.assertIn("compiler", key_names)
        self.assertIn("os", key_names)

        vals = self.tsdb.list_parameter_values(session, "compiler")
        val_strs = [v.value for v in vals]
        self.assertIn("clang-21", val_strs)
        session.close()

    def test_upsert_is_idempotent(self):
        """Upserting the same key/value twice should not fail."""
        session = self.Session()
        params = {"arch": "x86_64"}
        self.tsdb._upsert_parameter_keys_and_values(session, params)
        session.commit()
        self.tsdb._upsert_parameter_keys_and_values(session, params)
        session.commit()

        vals = self.tsdb.list_parameter_values(session, "arch")
        self.assertEqual(len(vals), 1)
        session.close()

    def test_upsert_empty_params(self):
        """Empty params should be a no-op."""
        session = self.Session()
        self.tsdb._upsert_parameter_keys_and_values(session, {})
        session.commit()
        session.close()


class TestDashboardCardCRUD(_CRUDTestBase):
    """Tests for dashboard card CRUD."""

    def test_get_set_dashboard_cards(self):
        session = self.Session()
        cards = self.tsdb.get_dashboard_cards(session)
        self.assertEqual(len(cards), 0)

        self.tsdb.set_dashboard_cards(session, [
            {"position": 0, "params": {"os": "linux"},
             "metric": "execution_time", "last_n": 100},
            {"position": 1, "params": {},
             "metric": "execution_time"},
        ])
        session.commit()

        cards = self.tsdb.get_dashboard_cards(session)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].position, 0)
        self.assertEqual(cards[0].params, {"os": "linux"})
        self.assertEqual(cards[0].last_n, 100)
        self.assertEqual(cards[1].position, 1)
        self.assertEqual(cards[1].last_n, 500)  # default
        session.close()

    def test_set_dashboard_cards_replaces(self):
        """set_dashboard_cards deletes existing and inserts new."""
        session = self.Session()
        self.tsdb.set_dashboard_cards(session, [
            {"position": 0, "params": {}, "metric": "execution_time"},
        ])
        session.commit()

        self.tsdb.set_dashboard_cards(session, [
            {"position": 0, "params": {}, "metric": "execution_time"},
            {"position": 1, "params": {}, "metric": "execution_time"},
        ])
        session.commit()

        cards = self.tsdb.get_dashboard_cards(session)
        self.assertEqual(len(cards), 2)
        session.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
