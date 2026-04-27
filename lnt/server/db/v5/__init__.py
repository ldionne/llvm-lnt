"""
v5 database layer.

Provides :class:`V5DB` (engine, sessions, schema loading) and
:class:`V5TestSuiteDB` (per-suite CRUD operations).

Postgres only.  No imports from v4 DB code.
"""

from __future__ import annotations

import base64
import datetime
import json
import re
import sys
import uuid as uuid_module
from typing import Any, Iterable

if sys.version_info >= (3, 12):
    from itertools import batched
else:
    from itertools import islice

    def batched(iterable, n):  # type: ignore[no-redef]
        it = iter(iterable)
        while batch := tuple(islice(it, n)):
            yield batch

import sqlalchemy
import sqlalchemy.exc
import sqlalchemy.orm
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import (
    SuiteModels,
    V5Schema,
    V5SchemaVersion,
    create_global_tables,
    create_suite_models,
    utcnow,
)
from .schema import TestSuiteSchema, parse_schema

DEFAULT_LIMIT = 1000

# Maximum number of names per IN-clause chunk for batch operations.
# Stays under psycopg2's 32,767 bind-parameter limit.
_BATCH_CHUNK_SIZE = 32_000

# Regression state values (see design D5).
REGRESSION_STATES = {
    0: "detected",
    1: "active",
    2: "not_to_be_fixed",
    3: "fixed",
    4: "false_positive",
}
VALID_REGRESSION_STATES = frozenset(REGRESSION_STATES)

_PARAM_KEY_RE = re.compile(r'^[a-zA-Z0-9_]+$')
_RESERVED_PARAM_NAMES = frozenset({
    'commit', 'test', 'metric', 'sort', 'cursor', 'limit', 'run', 'search',
})


def _escape_like(s: str) -> str:
    """Escape SQL special characters for use in ILIKE patterns.

    Replaces ``\\``, ``%``, and ``_`` with their escaped equivalents
    so the caller can safely use ``ESCAPE '\\'`` in the ILIKE clause.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def initialize_v5_database(path: str) -> None:
    """Create v5 global tables and seed rows.

    Idempotent -- safe to call on an already-initialized database.
    Called by ``lnt create --db-version 5.0``.
    """
    engine = sqlalchemy.create_engine(path)
    try:
        create_global_tables(engine)
        session = sqlalchemy.orm.sessionmaker(engine)()
        try:
            if session.query(V5SchemaVersion).get(1) is None:
                session.add(V5SchemaVersion(id=1, version=0))
                session.commit()
        except sqlalchemy.exc.IntegrityError:
            session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()


class V5DB:
    """Top-level database handle for a v5 LNT instance.

    Owns the SQLAlchemy engine, session factory, and a dict of per-suite
    :class:`V5TestSuiteDB` wrappers.  Schemas are stored in the database
    (``v5_schema`` table), not on the filesystem.
    """

    def __init__(self, path: str, config: Any):
        self.path = path
        self.config = config
        self.engine = sqlalchemy.create_engine(
            path,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.sessionmaker = sqlalchemy.orm.sessionmaker(self.engine)
        self.testsuite: dict[str, V5TestSuiteDB] = {}
        self._schema_version: int | None = None

        try:
            self._load_schemas_from_db()
        except sqlalchemy.exc.ProgrammingError as e:
            self.engine.dispose()
            if "v5_schema" in str(e):
                raise RuntimeError(
                    "v5 database not initialized. "
                    "Run: lnt create --db-version 5.0"
                ) from e
            raise
        except Exception:
            self.engine.dispose()
            raise

    # -- schema storage --------------------------------------------------------

    @staticmethod
    def _schema_to_dict(schema: TestSuiteSchema) -> dict[str, Any]:
        """Serialize a TestSuiteSchema to a JSON-serializable dict."""
        return {
            "name": schema.name,
            "metrics": [
                {
                    "name": m.name,
                    "type": m.type,
                    **({"display_name": m.display_name} if m.display_name else {}),
                    **({"unit": m.unit} if m.unit else {}),
                    **({"unit_abbrev": m.unit_abbrev} if m.unit_abbrev else {}),
                    **({"bigger_is_better": True} if m.bigger_is_better else {}),
                }
                for m in schema.metrics
            ],
            "commit_fields": [
                {
                    "name": cf.name,
                    **({"type": cf.type} if cf.type != "default" else {}),
                    **({"searchable": True} if cf.searchable else {}),
                    **({"display": True} if cf.display else {}),
                }
                for cf in schema.commit_fields
            ],
        }

    def _load_schemas_from_db(self) -> None:
        """Read all rows from ``v5_schema``, parse each, and build models.

        Read ordering matters for multi-process safety under PostgreSQL's
        default READ COMMITTED isolation: version is read *before* schemas
        so that a concurrent commit between the two reads leaves the cached
        version behind (triggering a harmless reload next request) rather
        than ahead (silently missing the new suite).  ``_schema_version``
        is set *last* so that a failure during the rebuild leaves it stale,
        causing the next ``ensure_fresh`` call to retry.
        """
        session = self.sessionmaker()
        try:
            ver = session.query(V5SchemaVersion).get(1)
            version = ver.version if ver else 0
            rows = session.query(V5Schema).all()

            new_suites: dict[str, V5TestSuiteDB] = {}
            for row in rows:
                data = json.loads(row.schema_json)
                schema = parse_schema(data)
                models = create_suite_models(schema)
                tsdb = V5TestSuiteDB(self, schema, models)
                new_suites[schema.name] = tsdb

            self.testsuite = new_suites
            self._schema_version = version
        finally:
            session.close()

    def _check_schema_version(self, session: sqlalchemy.orm.Session) -> bool:
        """Return True if the cached schema version is stale."""
        row = session.query(V5SchemaVersion).get(1)
        current = row.version if row else 0
        return current != self._schema_version

    @staticmethod
    def _bump_schema_version(session: sqlalchemy.orm.Session) -> None:
        """Increment the version counter (caller must commit)."""
        row = session.query(V5SchemaVersion).get(1)
        if row is None:
            row = V5SchemaVersion(id=1, version=1)
            session.add(row)
        else:
            row.version = row.version + 1

    def ensure_fresh(self, session: sqlalchemy.orm.Session) -> None:
        """Reload schemas from the DB if the cached version is stale.

        Call this once per request (e.g. in middleware) so that all
        endpoints see up-to-date test-suite definitions, even when
        another worker created or deleted a suite.
        """
        if self._check_schema_version(session):
            self._load_schemas_from_db()

    def get_suite(self, name: str) -> V5TestSuiteDB | None:
        """Return a suite by name, or None."""
        return self.testsuite.get(name)

    def create_suite(
        self,
        session: sqlalchemy.orm.Session,
        schema: TestSuiteSchema,
    ) -> V5TestSuiteDB:
        """Persist a new suite schema in the DB and create its tables."""
        if schema.name in self.testsuite:
            raise ValueError(f"suite {schema.name!r} already exists")
        schema_dict = self._schema_to_dict(schema)
        row = V5Schema(
            name=schema.name,
            schema_json=json.dumps(schema_dict),
            created_at=utcnow(),
        )
        session.add(row)
        self._bump_schema_version(session)
        session.flush()

        models = create_suite_models(schema)
        models.base.metadata.create_all(self.engine)
        tsdb = V5TestSuiteDB(self, schema, models)
        self.testsuite[schema.name] = tsdb

        ver = session.query(V5SchemaVersion).get(1)
        self._schema_version = ver.version if ver else 0

        return tsdb

    def delete_suite(
        self,
        session: sqlalchemy.orm.Session,
        name: str,
    ) -> None:
        """Delete a suite schema from the DB and drop its tables."""
        tsdb = self.testsuite.get(name)
        if tsdb is None:
            raise ValueError(f"suite {name!r} does not exist")

        row = session.query(V5Schema).get(name)
        if row is not None:
            session.delete(row)
        self._bump_schema_version(session)
        session.flush()

        tsdb.models.base.metadata.drop_all(self.engine)
        del self.testsuite[name]

        ver = session.query(V5SchemaVersion).get(1)
        self._schema_version = ver.version if ver else 0

    # -- session helpers -------------------------------------------------------

    def make_session(self, expire_on_commit: bool = True) -> sqlalchemy.orm.Session:
        """Return a new SQLAlchemy session."""
        return self.sessionmaker(expire_on_commit=expire_on_commit)

    def close(self) -> None:
        self.engine.dispose()


def _validate_regression_state(state: int) -> None:
    """Raise ValueError if *state* is not a valid regression state."""
    if state not in VALID_REGRESSION_STATES:
        raise ValueError(
            f"invalid regression state {state!r}; "
            f"valid states: {sorted(VALID_REGRESSION_STATES)}"
        )


class V5TestSuiteDB:
    """Per-suite database operations for the v5 layer.

    Provides a clean CRUD interface consumed by the v5 API endpoints.
    """

    def __init__(self, v5db: V5DB, schema: TestSuiteSchema, models: SuiteModels):
        self.v5db = v5db
        self.name = schema.name
        self.schema = schema
        self.models = models
        self._commit_field_names: frozenset[str] = frozenset(cf.name for cf in schema.commit_fields)
        self._metric_names: frozenset[str] = frozenset(m.name for m in schema.metrics)
        self.Commit = models.Commit
        self.Run = models.Run
        self.Test = models.Test
        self.Sample = models.Sample
        self.Profile = models.Profile
        self.Regression = models.Regression
        self.RegressionIndicator = models.RegressionIndicator
        self.ParameterKey = models.ParameterKey
        self.ParameterValue = models.ParameterValue
        self.DashboardCard = models.DashboardCard

    # ===================================================================
    # Field / metric validation
    # ===================================================================

    def _validate_commit_fields(self, keys: Iterable[str]) -> None:
        """Raise ValueError if *keys* contains names not in the schema."""
        unknown = set(keys) - self._commit_field_names
        if unknown:
            raise ValueError(
                f"Unknown commit field(s): {', '.join(sorted(unknown))}. "
                f"Valid names: {', '.join(sorted(self._commit_field_names))}"
            )

    def _validate_metric_names(self, keys: Iterable[str]) -> None:
        """Raise ValueError if *keys* contains names not in the schema."""
        unknown = set(keys) - self._metric_names
        if unknown:
            raise ValueError(
                f"Unknown metric(s): {', '.join(sorted(unknown))}. "
                f"Valid names: {', '.join(sorted(self._metric_names))}"
            )

    # ===================================================================
    # Commits
    # ===================================================================

    def get_or_create_commit(
        self,
        session: sqlalchemy.orm.Session,
        commit: str,
        **metadata: Any,
    ):
        """Return existing Commit or create a new one.

        *metadata* keys correspond to ``commit_fields`` defined in the schema.
        On creation, all metadata is stored.  If the commit already exists,
        metadata is NOT overwritten (first-write-wins).
        """
        if not commit:
            raise ValueError("commit string must be non-empty")
        existing = (
            session.query(self.Commit)
            .filter(self.Commit.commit == commit)
            .first()
        )
        if existing is not None:
            return existing

        obj = self.Commit()
        obj.commit = commit
        # ordinal is always NULL on creation
        self._validate_commit_fields(metadata.keys())
        for key, value in metadata.items():
            setattr(obj, key, value)
        try:
            with session.begin_nested():
                session.add(obj)
                session.flush()
        except sqlalchemy.exc.IntegrityError:
            # Race condition: another session created it; fetch and return.
            existing = (
                session.query(self.Commit)
                .filter(self.Commit.commit == commit)
                .first()
            )
            if existing is None:
                raise  # pragma: no cover
            return existing
        return obj

    def get_commit(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        commit: str | None = None,
    ):
        """Fetch a single Commit by id or commit string.  Returns None if not found."""
        q = session.query(self.Commit)
        if id is not None:
            return q.filter(self.Commit.id == id).first()
        if commit is not None:
            return q.filter(self.Commit.commit == commit).first()
        raise ValueError("must specify id or commit")

    def get_commits_by_values(
        self,
        session: sqlalchemy.orm.Session,
        commit_values: list[str],
    ) -> list:
        """Fetch multiple Commits by their commit strings in a single query.

        Returns a list of Commit objects for values that exist.
        Order is not guaranteed.
        """
        if not commit_values:
            return []
        return (
            session.query(self.Commit)
            .filter(self.Commit.commit.in_(commit_values))
            .all()
        )

    def update_commit(
        self,
        session: sqlalchemy.orm.Session,
        commit_obj,
        *,
        ordinal: int | None = None,
        clear_ordinal: bool = False,
        tag: str | None = None,
        clear_tag: bool = False,
        **commit_fields: Any,
    ):
        """Update mutable fields on a Commit.

        *ordinal* sets the ordering position.  Pass ``clear_ordinal=True``
        to explicitly set ordinal to ``None``.
        *tag* sets the human-readable label.  Pass ``clear_tag=True``
        to explicitly set tag to ``None``.
        Additional keyword arguments correspond to ``commit_fields`` defined
        in the schema and update the matching columns.
        """
        if clear_ordinal:
            commit_obj.ordinal = None
        elif ordinal is not None:
            commit_obj.ordinal = ordinal
        if clear_tag:
            commit_obj.tag = None
        elif tag is not None:
            commit_obj.tag = tag
        self._validate_commit_fields(commit_fields.keys())
        for key, value in commit_fields.items():
            setattr(commit_obj, key, value)
        session.flush()
        return commit_obj

    def list_commits(
        self,
        session: sqlalchemy.orm.Session,
        *,
        search: str | None = None,
        ordinal_range: tuple[int, int] | None = None,
        limit: int | None = None,
    ) -> list:
        """List commits with optional search / ordinal-range filtering.

        *search* performs case-insensitive OR substring matching across the
        ``commit`` column, the built-in ``tag`` column, and all ``searchable``
        commit_fields.
        """
        q = session.query(self.Commit)

        if search:
            escaped = _escape_like(search)
            pattern = f"%{escaped}%"
            clauses = [self.Commit.commit.ilike(pattern, escape="\\")]
            clauses.append(self.Commit.tag.ilike(pattern, escape="\\"))
            for cf in self.schema.searchable_commit_fields:
                col = getattr(self.Commit, cf.name)
                clauses.append(col.ilike(pattern, escape="\\"))
            q = q.filter(or_(*clauses))

        if ordinal_range is not None:
            lo, hi = ordinal_range
            q = q.filter(
                self.Commit.ordinal.isnot(None),
                self.Commit.ordinal >= lo,
                self.Commit.ordinal <= hi,
            )

        q = q.order_by(self.Commit.id)

        q = q.limit(limit if limit is not None else DEFAULT_LIMIT)

        return q.all()

    def delete_commit(
        self,
        session: sqlalchemy.orm.Session,
        commit_id: int,
    ) -> None:
        """Delete a commit by ID (cascades to runs and samples).

        Raises ``ValueError`` if any Regressions reference this commit
        (via ``commit_id``), or if any RegressionIndicators reference
        runs at this commit.  Those must be updated/removed first.
        """
        commit = session.query(self.Commit).get(commit_id)
        if commit is None:
            return

        reg_count = (
            session.query(self.Regression)
            .filter(self.Regression.commit_id == commit_id)
            .count()
        )
        if reg_count > 0:
            raise ValueError(
                f"Cannot delete commit {commit_id}: "
                f"{reg_count} Regression(s) reference it; "
                f"update or delete them first"
            )

        # Check for indicators referencing runs at this commit.
        indicator_count = (
            session.query(self.RegressionIndicator)
            .join(self.Run, self.RegressionIndicator.run_id == self.Run.id)
            .filter(self.Run.commit_id == commit_id)
            .count()
        )
        if indicator_count > 0:
            raise ValueError(
                f"Cannot delete commit {commit_id}: "
                f"{indicator_count} RegressionIndicator(s) reference runs "
                f"at this commit; remove them first"
            )

        session.delete(commit)
        session.flush()

    # ===================================================================
    # Runs
    # ===================================================================

    def create_run(
        self,
        session: sqlalchemy.orm.Session,
        *,
        commit,
        submitted_at: datetime.datetime | None = None,
        run_parameters: dict[str, Any] | None = None,
    ):
        """Create a new Run attached to *commit*.

        *commit* is required -- every run must have a commit (design D2).
        """
        if commit is None:
            raise ValueError("commit is required (every run must have a commit)")
        run = self.Run()
        run.uuid = str(uuid_module.uuid4())
        run.commit_id = commit.id
        run.submitted_at = submitted_at or utcnow()
        run.run_parameters = run_parameters or {}
        session.add(run)
        session.flush()
        return run

    def get_run(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        uuid: str | None = None,
    ):
        """Fetch a single Run by id or uuid."""
        q = session.query(self.Run)
        if id is not None:
            return q.filter(self.Run.id == id).first()
        if uuid is not None:
            return q.filter(self.Run.uuid == uuid).first()
        raise ValueError("must specify id or uuid")

    def list_runs(
        self,
        session: sqlalchemy.orm.Session,
        *,
        params: dict[str, str | list[str]] | None = None,
        commit_id: int | None = None,
        limit: int | None = None,
    ) -> list:
        """List runs with optional filters.

        *params* is a dict of parameter key -> value(s) for JSONB containment
        filtering. Multiple values for the same key are OR-combined; different
        keys are AND-combined.
        """
        q = session.query(self.Run)
        if params:
            q = q.filter(self._build_params_filter(self.Run, params))
        if commit_id is not None:
            q = q.filter(self.Run.commit_id == commit_id)
        q = q.order_by(self.Run.id)
        q = q.limit(limit if limit is not None else DEFAULT_LIMIT)
        return q.all()

    def delete_run(self, session: sqlalchemy.orm.Session, run_id: int) -> None:
        """Delete a run by ID (cascades to samples).

        Raises ``ValueError`` if any RegressionIndicators reference this run.
        Indicators must be removed first.
        """
        run = session.query(self.Run).get(run_id)
        if run is None:
            return

        indicator_count = (
            session.query(self.RegressionIndicator)
            .filter(self.RegressionIndicator.run_id == run_id)
            .count()
        )
        if indicator_count > 0:
            raise ValueError(
                f"Cannot delete run {run_id}: "
                f"{indicator_count} RegressionIndicator(s) reference it; "
                f"remove them first"
            )

        session.delete(run)
        session.flush()

    # ===================================================================
    # Tests & Samples
    # ===================================================================

    def get_or_create_tests(
        self,
        session: sqlalchemy.orm.Session,
        names: Iterable[str],
    ) -> dict[str, int]:
        """Resolve test names to IDs, creating missing tests in bulk.

        Returns a ``{name: id}`` mapping for every name in *names*.
        Duplicate names in *names* are handled (deduplicated internally).

        Uses INSERT ... ON CONFLICT DO NOTHING for safe concurrent creation.

        Note: this uses a Core INSERT, bypassing the ORM identity map.
        Only integer IDs are returned, not ORM objects.

        Raises ``RuntimeError`` if a name cannot be resolved after insert
        (indicates a concurrent DELETE, which should not happen in normal
        operation).
        """
        unique_names = list(set(names))
        if not unique_names:
            return {}

        name_to_id: dict[str, int] = {}

        for chunk in batched(unique_names, _BATCH_CHUNK_SIZE):
            existing = (
                session.query(self.Test.id, self.Test.name)
                .filter(self.Test.name.in_(chunk))
                .all()
            )
            for test_id, test_name in existing:
                name_to_id[test_name] = test_id

            missing = [n for n in chunk if n not in name_to_id]
            if not missing:
                continue

            # ON CONFLICT DO NOTHING handles races with concurrent inserts.
            stmt = (
                pg_insert(self.Test.__table__)
                .values([{"name": n} for n in missing])
                .on_conflict_do_nothing(index_elements=["name"])
            )
            session.execute(stmt)

            # Re-SELECT to pick up rows from concurrent inserts we lost
            # the race to (RETURNING only covers actually-inserted rows).
            new_rows = (
                session.query(self.Test.id, self.Test.name)
                .filter(self.Test.name.in_(missing))
                .all()
            )
            for test_id, test_name in new_rows:
                name_to_id[test_name] = test_id

            still_missing = [n for n in missing if n not in name_to_id]
            if still_missing:
                raise RuntimeError(
                    f"Failed to resolve test names after insert "
                    f"(concurrent DELETE?): {still_missing[:5]}"
                )

        return name_to_id

    def get_test(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        name: str | None = None,
    ):
        """Fetch a single Test by id or name.  Returns None if not found."""
        q = session.query(self.Test)
        if id is not None:
            return q.filter(self.Test.id == id).first()
        if name is not None:
            return q.filter(self.Test.name == name).first()
        raise ValueError("must specify id or name")

    def list_samples(
        self,
        session: sqlalchemy.orm.Session,
        *,
        run_id: int | None = None,
        test_id: int | None = None,
        limit: int | None = None,
    ) -> list:
        """List samples with optional filters."""
        q = session.query(self.Sample)
        if run_id is not None:
            q = q.filter(self.Sample.run_id == run_id)
        if test_id is not None:
            q = q.filter(self.Sample.test_id == test_id)
        q = q.order_by(self.Sample.id)
        q = q.limit(limit if limit is not None else DEFAULT_LIMIT)
        return q.all()

    def create_samples(
        self,
        session: sqlalchemy.orm.Session,
        run,
        samples: list[dict[str, Any]],
    ) -> None:
        """Create Sample rows for *run*.

        Each dict in *samples* must have ``test_id`` plus metric fields.
        Uses a Core multi-row INSERT for performance.
        """
        if not samples:
            return

        all_metric_keys: set[str] = set()
        for s in samples:
            all_metric_keys.update(s)
        all_metric_keys.discard("test_id")
        self._validate_metric_names(all_metric_keys)

        # Multi-row VALUES requires uniform keys across all dicts.
        all_keys = {"run_id", "test_id"} | all_metric_keys
        template = dict.fromkeys(all_keys)
        template["run_id"] = run.id
        rows = [{**template, **s} for s in samples]

        # Chunk to stay under psycopg2's 32,767 bind-parameter limit.
        cols_per_row = len(all_keys)
        chunk_size = max(1, _BATCH_CHUNK_SIZE // cols_per_row)

        for chunk in batched(rows, chunk_size):
            stmt = pg_insert(self.Sample.__table__).values(chunk)
            session.execute(stmt)

    # ===================================================================
    # Profile CRUD
    # ===================================================================

    _MAX_PROFILE_SIZE = 50 * 1024 * 1024  # 50 MB

    def _create_profiles(
        self,
        session: sqlalchemy.orm.Session,
        run,
        profiles: list[tuple],
    ) -> list:
        """Create Profile rows from (test_name, test_id, base64_data) tuples.

        Validates base64 encoding, size limit, and format version byte.
        """
        from .profile import ProfileData, ProfileParseError

        created = []
        for test_name, test_id, b64_data in profiles:
            if not isinstance(b64_data, str) or not b64_data:
                raise ValueError(
                    f"profile for test '{test_name}' must be a non-empty string"
                )
            try:
                raw = base64.b64decode(b64_data)
            except Exception:
                raise ValueError(
                    f"invalid base64 in profile for test '{test_name}'"
                )
            if len(raw) > self._MAX_PROFILE_SIZE:
                raise ValueError(
                    f"profile for test '{test_name}' exceeds 50 MB size limit"
                )
            try:
                ProfileData.validate_version(raw)
            except ProfileParseError as e:
                raise ValueError(
                    f"invalid profile for test '{test_name}': {e}"
                )
            p = self.Profile()
            p.run_id = run.id
            p.test_id = test_id
            p.created_at = utcnow()
            p.data = raw
            created.append(p)
        session.add_all(created)
        session.flush()
        return created

    def get_profile(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        uuid: str | None = None,
        load_data: bool = False,
    ):
        """Fetch a single Profile by id or uuid.

        When *load_data* is True, eagerly loads the deferred ``data``
        column and joins the ``test`` and ``run`` relations to avoid
        lazy-load queries.
        """
        from sqlalchemy.orm import joinedload, undefer

        q = session.query(self.Profile)
        if load_data:
            q = q.options(
                undefer(self.Profile.data),
                joinedload(self.Profile.run),
                joinedload(self.Profile.test),
            )
        if id is not None:
            return q.filter(self.Profile.id == id).first()
        if uuid is not None:
            return q.filter(self.Profile.uuid == uuid).first()
        raise ValueError("must specify id or uuid")

    def get_profiles_for_run(
        self,
        session: sqlalchemy.orm.Session,
        run,
    ) -> list[tuple[str, str]]:
        """Return (uuid, test_name) pairs for all profiles in a run.

        Uses column projection -- no ORM objects, no blob loading.
        """
        return (
            session.query(self.Profile.uuid, self.Test.name)
            .join(self.Test, self.Profile.test_id == self.Test.id)
            .filter(self.Profile.run_id == run.id)
            .all()
        )

    # ===================================================================
    # Time-series query
    # ===================================================================

    def query_time_series(
        self,
        session: sqlalchemy.orm.Session,
        test,
        metric: str,
        *,
        params: dict[str, str | list[str]] | None = None,
        commit_range: tuple[int, int] | None = None,
        time_range: tuple[datetime.datetime, datetime.datetime] | None = None,
        sort: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query time-series data for a (parameter set, test, metric) combination.

        Returns a list of dicts with keys: ``commit``, ``ordinal``, ``tag``,
        ``value``, ``run_id``, ``submitted_at``.

        *params* is a dict for JSONB containment filtering on ``run_parameters``.
        *commit_range* filters by ordinal [lo, hi].  When sorting by ordinal,
        commits without ordinals are excluded.
        """
        metric_col = getattr(self.Sample, metric, None)
        if metric_col is None:
            raise ValueError(f"unknown metric {metric!r}")

        q = (
            session.query(
                self.Commit.commit,
                self.Commit.ordinal,
                self.Commit.tag,
                metric_col.label("value"),
                self.Run.id.label("run_id"),
                self.Run.submitted_at,
            )
            .select_from(self.Sample)
            .join(self.Run, self.Sample.run_id == self.Run.id)
            .join(self.Commit, self.Run.commit_id == self.Commit.id)
            .filter(self.Sample.test_id == test.id)
            .filter(metric_col.isnot(None))
        )

        if params:
            q = q.filter(self._build_params_filter(self.Run, params))

        if commit_range is not None:
            lo, hi = commit_range
            q = q.filter(
                self.Commit.ordinal.isnot(None),
                self.Commit.ordinal >= lo,
                self.Commit.ordinal <= hi,
            )

        if time_range is not None:
            start, end = time_range
            q = q.filter(
                self.Run.submitted_at >= start,
                self.Run.submitted_at <= end,
            )

        if sort == "ordinal":
            q = q.filter(self.Commit.ordinal.isnot(None))
            q = q.order_by(self.Commit.ordinal)
        elif sort == "submitted_at":
            q = q.order_by(self.Run.submitted_at)
        elif sort is not None:
            raise ValueError("Unknown sort field: %r" % sort)
        else:
            q = q.order_by(self.Sample.id)

        if limit is not None:
            q = q.limit(limit)

        results = []
        for row in q.all():
            results.append({
                "commit": row.commit,
                "ordinal": row.ordinal,
                "tag": row.tag,
                "value": row.value,
                "run_id": row.run_id,
                "submitted_at": row.submitted_at,
            })
        return results

    def query_trends(
        self,
        session: sqlalchemy.orm.Session,
        metric: str,
        *,
        params: dict[str, str | list[str]] | None = None,
        last_n: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query geomean-aggregated trend data grouped by commit.

        Computes ``exp(avg(ln(metric)))`` over all samples for each
        commit.  Only positive metric values are included (required for
        ``ln``).  Only commits with a non-null ordinal are included.

        *params* is a dict for JSONB containment filtering on ``run_parameters``.
        *last_n* limits results to the most recent N commits by ordinal.
        *limit* caps the total number of rows returned.

        Returns a list of dicts with keys: ``commit``, ``ordinal``, ``tag``,
        ``value``, ``submitted_at``.
        """
        from sqlalchemy import func

        metric_col = getattr(self.Sample, metric, None)
        if metric_col is None:
            raise ValueError(f"unknown metric {metric!r}")

        q = (
            session.query(
                self.Commit.id.label("commit_id"),
                self.Commit.commit,
                self.Commit.ordinal,
                self.Commit.tag,
                func.exp(func.avg(func.ln(metric_col))).label("value"),
                func.max(self.Run.submitted_at).label("submitted_at"),
            )
            .select_from(self.Sample)
            .join(self.Run, self.Sample.run_id == self.Run.id)
            .join(self.Commit, self.Run.commit_id == self.Commit.id)
            .filter(metric_col > 0)
            .filter(self.Commit.ordinal.isnot(None))
        )

        if params:
            q = q.filter(self._build_params_filter(self.Run, params))

        if last_n is not None:
            # Find the ordinal cutoff: the Nth-highest ordinal.
            cutoff = (
                session.query(self.Commit.ordinal)
                .filter(self.Commit.ordinal.isnot(None))
                .order_by(self.Commit.ordinal.desc())
                .offset(last_n - 1)
                .limit(1)
                .scalar()
            )
            if cutoff is not None:
                q = q.filter(self.Commit.ordinal >= cutoff)
            # If cutoff is None, fewer than last_n commits exist -- return all.

        q = q.group_by(
            self.Commit.id,
            self.Commit.commit, self.Commit.ordinal,
            self.Commit.tag,
        )

        q = q.order_by(self.Commit.ordinal.asc())

        if limit is not None:
            q = q.limit(limit)

        results = []
        for row in q.all():
            results.append({
                "commit": row.commit,
                "ordinal": row.ordinal,
                "tag": row.tag,
                "value": row.value,
                "submitted_at": row.submitted_at,
            })
        return results

    # ===================================================================
    # Regressions (CRUD)
    # ===================================================================

    def create_regression(
        self,
        session: sqlalchemy.orm.Session,
        title: str | None,
        indicators: list[dict[str, Any]],
        *,
        bug: str | None = None,
        notes: str | None = None,
        commit,
        state: int = 0,
    ):
        """Create a Regression with the given indicators.

        Each dict in *indicators* must have keys ``run_id`` (int),
        ``test_id`` (int), and ``metric`` (str).

        *commit* is required -- a regression is always created at a specific
        commit.  All indicator runs must share this commit (validated here).
        """
        _validate_regression_state(state)
        if commit is None:
            raise ValueError("commit is required (a regression must have a commit)")
        reg = self.Regression()
        reg.uuid = str(uuid_module.uuid4())
        reg.title = title
        reg.bug = bug
        reg.notes = notes
        reg.state = state
        reg.commit_id = commit.id
        session.add(reg)
        session.flush()

        # Validate all indicator runs share the regression's commit.
        if indicators:
            run_ids = {ind["run_id"] for ind in indicators}
            runs = (
                session.query(self.Run.id, self.Run.commit_id)
                .filter(self.Run.id.in_(run_ids))
                .all()
            )
            found_ids = {r.id for r in runs}
            missing = run_ids - found_ids
            if missing:
                raise ValueError(
                    f"run_id(s) not found: {sorted(missing)}"
                )
            bad = [r.id for r in runs if r.commit_id != commit.id]
            if bad:
                raise ValueError(
                    f"indicator run(s) {bad} do not belong to "
                    f"the regression's commit"
                )

        ri_objects = []
        for ind in indicators:
            ri_objects.append(
                self._build_indicator(reg.id, ind["run_id"],
                                      ind["test_id"], ind["metric"]))
        session.add_all(ri_objects)
        session.flush()
        return reg

    def get_regression(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        uuid: str | None = None,
    ):
        """Fetch a single Regression by id or uuid."""
        q = session.query(self.Regression)
        if id is not None:
            return q.filter(self.Regression.id == id).first()
        if uuid is not None:
            return q.filter(self.Regression.uuid == uuid).first()
        raise ValueError("must specify id or uuid")

    _UNSET = object()

    def update_regression(
        self,
        session: sqlalchemy.orm.Session,
        regression,
        *,
        title: Any = _UNSET,
        bug: Any = _UNSET,
        notes: Any = _UNSET,
        commit: Any = _UNSET,
        state: int | None = None,
    ):
        """Update mutable fields on a Regression.

        For *title*, *bug*, and *notes*: pass a value to set, ``None`` to
        clear, or omit (default ``_UNSET``) to leave unchanged.
        *commit* must be a Commit object (cannot be set to None since
        commit_id is NOT NULL). Omit to leave unchanged.
        *state* uses ``None`` as "leave unchanged" (state cannot be null).

        When *commit* is changed, all existing indicators are re-validated:
        each indicator's run must belong to the new commit.
        """
        if title is not self._UNSET:
            regression.title = title
        if bug is not self._UNSET:
            regression.bug = bug
        if notes is not self._UNSET:
            regression.notes = notes
        if commit is not self._UNSET:
            if commit is None:
                raise ValueError(
                    "commit_id is NOT NULL -- cannot clear the commit on a regression"
                )
            regression.commit_id = commit.id
            # Re-validate existing indicators against the new commit.
            bad_runs = (
                session.query(self.Run.id)
                .filter(
                    self.Run.id.in_(
                        session.query(self.RegressionIndicator.run_id)
                        .filter_by(regression_id=regression.id)
                    ),
                    self.Run.commit_id != commit.id,
                )
                .all()
            )
            if bad_runs:
                raise ValueError(
                    "Cannot change commit: %d indicator run(s) belong to a "
                    "different commit" % len(bad_runs)
                )
        if state is not None:
            _validate_regression_state(state)
            regression.state = state
        session.flush()
        return regression

    def list_regressions(
        self,
        session: sqlalchemy.orm.Session,
        *,
        state: int | None = None,
        limit: int | None = None,
    ) -> list:
        """List regressions, optionally filtered by state."""
        q = session.query(self.Regression)
        if state is not None:
            q = q.filter(self.Regression.state == state)
        return q.order_by(self.Regression.id).limit(limit if limit is not None else DEFAULT_LIMIT).all()

    def delete_regression(
        self,
        session: sqlalchemy.orm.Session,
        regression_id: int,
    ) -> None:
        """Delete a regression by ID (cascades to indicators)."""
        reg = session.query(self.Regression).get(regression_id)
        if reg is not None:
            session.delete(reg)
            session.flush()

    def _build_indicator(self, regression_id, run_id, test_id, metric):
        """Construct a RegressionIndicator object (not yet added to session)."""
        ri = self.RegressionIndicator()
        ri.uuid = str(uuid_module.uuid4())
        ri.regression_id = regression_id
        ri.run_id = run_id
        ri.test_id = test_id
        ri.metric = metric
        return ri

    def add_regression_indicator(
        self,
        session: sqlalchemy.orm.Session,
        regression,
        run_id: int,
        test_id: int,
        metric: str,
    ):
        """Add an indicator to a Regression.

        Validates that the run's commit matches the regression's commit.
        Returns the created RegressionIndicator.  Raises
        ``sqlalchemy.exc.IntegrityError`` if the (regression, run, test,
        metric) combination already exists.
        """
        run = session.query(self.Run).get(run_id)
        if run is None:
            raise ValueError(f"run_id {run_id} not found")
        if run.commit_id != regression.commit_id:
            raise ValueError(
                f"run {run_id} belongs to commit {run.commit_id}, "
                f"but regression requires commit {regression.commit_id}"
            )
        ri = self._build_indicator(regression.id, run_id, test_id, metric)
        session.add(ri)
        session.flush()
        return ri

    def add_regression_indicators_batch(
        self,
        session: sqlalchemy.orm.Session,
        regression,
        indicators: list[dict[str, Any]],
    ) -> list:
        """Add multiple indicators to a Regression, silently ignoring duplicates.

        Each dict must have keys ``run_id``, ``test_id``, ``metric``.
        Validates that all indicator runs share the regression's commit.
        Returns the list of newly created RegressionIndicator objects
        (excludes duplicates that were skipped).
        """
        if not indicators:
            return []

        # Validate all runs share the regression's commit.
        run_ids = {ind["run_id"] for ind in indicators}
        runs = (
            session.query(self.Run.id, self.Run.commit_id)
            .filter(self.Run.id.in_(run_ids))
            .all()
        )
        found_ids = {r.id for r in runs}
        missing = run_ids - found_ids
        if missing:
            raise ValueError(f"run_id(s) not found: {sorted(missing)}")
        bad = [r.id for r in runs if r.commit_id != regression.commit_id]
        if bad:
            raise ValueError(
                f"indicator run(s) {bad} do not belong to "
                f"the regression's commit"
            )

        # Fetch all existing indicators for this regression in one query.
        existing_rows = (
            session.query(
                self.RegressionIndicator.run_id,
                self.RegressionIndicator.test_id,
                self.RegressionIndicator.metric,
            )
            .filter_by(regression_id=regression.id)
            .all()
        )
        existing_keys = {
            (r.run_id, r.test_id, r.metric) for r in existing_rows
        }

        created = []
        for ind in indicators:
            key = (ind["run_id"], ind["test_id"], ind["metric"])
            if key in existing_keys:
                continue
            ri = self._build_indicator(
                regression.id, ind["run_id"],
                ind["test_id"], ind["metric"])
            session.add(ri)
            created.append(ri)
            existing_keys.add(key)
        session.flush()
        return created

    def remove_regression_indicator(
        self,
        session: sqlalchemy.orm.Session,
        regression_id: int,
        indicator_uuid: str,
    ) -> bool:
        """Remove an indicator from a regression by UUID.

        Returns True if an indicator was removed, False if none matched.
        """
        count = (
            session.query(self.RegressionIndicator)
            .filter(
                self.RegressionIndicator.regression_id == regression_id,
                self.RegressionIndicator.uuid == indicator_uuid,
            )
            .delete()
        )
        if count:
            session.flush()
        return count > 0

    def get_regression_indicator(
        self,
        session: sqlalchemy.orm.Session,
        *,
        id: int | None = None,
        uuid: str | None = None,
    ):
        """Fetch a single RegressionIndicator by id or uuid."""
        q = session.query(self.RegressionIndicator)
        if id is not None:
            return q.filter(self.RegressionIndicator.id == id).first()
        if uuid is not None:
            return q.filter(self.RegressionIndicator.uuid == uuid).first()
        raise ValueError("must specify id or uuid")

    # ===================================================================
    # Parameter validation and upsert
    # ===================================================================

    def _validate_run_parameters(self, run_parameters: dict[str, Any]) -> dict[str, str]:
        """Validate and coerce run parameters.

        Keys must match ``[a-zA-Z0-9_]+`` and must not use reserved names.
        All values are coerced to strings via ``str()``.
        Returns the coerced dict.
        """
        coerced: dict[str, str] = {}
        for key, value in run_parameters.items():
            if not _PARAM_KEY_RE.match(key):
                raise ValueError(
                    f"invalid run_parameters key {key!r}: "
                    f"must match [a-zA-Z0-9_]+"
                )
            if key in _RESERVED_PARAM_NAMES:
                raise ValueError(
                    f"run_parameters key {key!r} is reserved "
                    f"(cannot use: {sorted(_RESERVED_PARAM_NAMES)})"
                )
            coerced[key] = str(value)
        return coerced

    def _upsert_parameter_keys_and_values(
        self,
        session: sqlalchemy.orm.Session,
        run_parameters: dict[str, str],
    ) -> None:
        """Batch upsert parameter keys and (key, value) pairs.

        Uses INSERT ... ON CONFLICT DO NOTHING for concurrency safety.
        """
        if not run_parameters:
            return

        # Upsert keys
        key_rows = [{"key": k} for k in run_parameters]
        stmt = (
            pg_insert(self.ParameterKey.__table__)
            .values(key_rows)
            .on_conflict_do_nothing(index_elements=["key"])
        )
        session.execute(stmt)

        # Fetch key IDs for the values upsert
        keys = list(run_parameters.keys())
        key_id_rows = (
            session.query(self.ParameterKey.id, self.ParameterKey.key)
            .filter(self.ParameterKey.key.in_(keys))
            .all()
        )
        key_to_id = {r.key: r.id for r in key_id_rows}

        # Upsert (key_id, value) pairs
        value_rows = [
            {"key_id": key_to_id[k], "value": v}
            for k, v in run_parameters.items()
            if k in key_to_id
        ]
        if value_rows:
            stmt = (
                pg_insert(self.ParameterValue.__table__)
                .values(value_rows)
                .on_conflict_do_nothing(
                    constraint=f"uq_{self.name}_pv_key_value",
                )
            )
            session.execute(stmt)

    # ===================================================================
    # Parameter query helpers
    # ===================================================================

    @staticmethod
    def _build_params_filter(model_class, params: dict[str, str | list[str]]):
        """Build a SQLAlchemy filter for JSONB containment on run_parameters.

        For each key with one value, emit a single ``@>`` clause.
        For each key with multiple values, emit ``OR`` of ``@>`` clauses.
        Then ``AND`` all key clauses together.

        *model_class* must have a ``run_parameters`` column.

        Returns ``None`` if *params* is empty.
        """
        if not params:
            return None

        from sqlalchemy import and_

        key_clauses = []
        for key, values in params.items():
            if isinstance(values, str):
                values = [values]
            if len(values) == 1:
                key_clauses.append(
                    model_class.run_parameters.op("@>")(
                        sqlalchemy.type_coerce({key: values[0]}, JSONB)
                    )
                )
            else:
                or_clauses = [
                    model_class.run_parameters.op("@>")(
                        sqlalchemy.type_coerce({key: v}, JSONB)
                    )
                    for v in values
                ]
                key_clauses.append(or_(*or_clauses))

        if len(key_clauses) == 1:
            return key_clauses[0]
        return and_(*key_clauses)

    # ===================================================================
    # Parameter discovery
    # ===================================================================

    def list_parameter_keys(
        self,
        session: sqlalchemy.orm.Session,
        *,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list:
        """Query ParameterKey with optional ILIKE prefix match."""
        q = session.query(self.ParameterKey)
        if search:
            escaped = _escape_like(search)
            pattern = f"{escaped}%"
            q = q.filter(self.ParameterKey.key.ilike(pattern, escape="\\"))
        q = q.order_by(self.ParameterKey.key)
        q = q.offset(offset).limit(limit)
        return q.all()

    def list_parameter_values(
        self,
        session: sqlalchemy.orm.Session,
        key_name: str,
        *,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list:
        """Query ParameterValue joined to ParameterKey for a given key name."""
        q = (
            session.query(self.ParameterValue)
            .join(self.ParameterKey,
                  self.ParameterValue.key_id == self.ParameterKey.id)
            .filter(self.ParameterKey.key == key_name)
        )
        if search:
            escaped = _escape_like(search)
            pattern = f"{escaped}%"
            q = q.filter(self.ParameterValue.value.ilike(pattern, escape="\\"))
        q = q.order_by(self.ParameterValue.value)
        q = q.offset(offset).limit(limit)
        return q.all()

    # ===================================================================
    # Dashboard CRUD
    # ===================================================================

    def get_dashboard_cards(
        self,
        session: sqlalchemy.orm.Session,
    ) -> list:
        """Query DashboardCard ordered by position."""
        return (
            session.query(self.DashboardCard)
            .order_by(self.DashboardCard.position)
            .all()
        )

    def set_dashboard_cards(
        self,
        session: sqlalchemy.orm.Session,
        cards: list[dict[str, Any]],
    ) -> list:
        """Delete all existing cards and insert new ones.

        Each dict in *cards* must have keys ``position``, ``params``,
        ``metric``, and optionally ``last_n``.
        """
        session.query(self.DashboardCard).delete()
        created = []
        for card_data in cards:
            card = self.DashboardCard()
            card.position = card_data["position"]
            card.params = card_data.get("params", {})
            card.metric = card_data["metric"]
            card.last_n = card_data.get("last_n", 500)
            session.add(card)
            created.append(card)
        session.flush()
        return created

    # ===================================================================
    # Bulk import (run submission) -- helpers
    # ===================================================================

    def _parse_commit_data(
        self,
        session: sqlalchemy.orm.Session,
        data: dict[str, Any],
    ):
        """Extract and get-or-create the Commit from the submission data.

        Returns the Commit object.
        """
        commit_str = data.get("commit")
        if not commit_str:
            raise ValueError("commit is required (every run must have a commit)")
        commit_field_data = data.get("commit_fields", {})
        return self.get_or_create_commit(session, commit_str, **commit_field_data)

    def _parse_tests_data(
        self,
        session: sqlalchemy.orm.Session,
        data: dict[str, Any],
        run,
    ) -> None:
        """Parse test entries and create all samples in a single batch.

        Metric values may be scalars or lists.  A list value (e.g.
        ``"execution_time": [0.1, 0.2]``) creates one Sample per element.
        All list values in a single test entry must have the same length;
        scalar values are repeated across the resulting Samples.
        """
        tests_data = data.get("tests", [])
        all_samples: list[dict[str, Any]] = []
        all_profiles: list[tuple] = []  # (test, base64_data)

        all_test_names: list[str] = []
        for test_entry in tests_data:
            test_name = test_entry.get("name")
            if not test_name:
                raise ValueError("each test entry must have a 'name'")
            all_test_names.append(test_name)

        name_to_id = self.get_or_create_tests(session, all_test_names)

        for test_entry in tests_data:
            test_name = test_entry["name"]
            test_id = name_to_id[test_name]

            # "profile" is a reserved key for profile binary data, not a metric.
            self._validate_metric_names(test_entry.keys() - {"name", "profile"})
            metrics: dict[str, Any] = {}
            profile_data_b64 = None
            for key, value in test_entry.items():
                if key == "name":
                    continue
                if key == "profile":
                    profile_data_b64 = value
                    continue
                metrics[key] = value

            list_len = None
            for key, value in metrics.items():
                if isinstance(value, list):
                    if list_len is None:
                        list_len = len(value)
                    elif len(value) != list_len:
                        raise ValueError(
                            f"metric lists for test '{test_name}' have "
                            f"inconsistent lengths"
                        )

            if list_len is not None and list_len == 0:
                raise ValueError(
                    f"metric lists for test '{test_name}' must not be empty"
                )

            if list_len is None:
                sample_dict: dict[str, Any] = {"test_id": test_id}
                sample_dict.update(metrics)
                all_samples.append(sample_dict)
            else:
                for i in range(list_len):
                    sample_dict = {"test_id": test_id}
                    for key, value in metrics.items():
                        sample_dict[key] = value[i] if isinstance(value, list) else value
                    all_samples.append(sample_dict)

            if profile_data_b64 is not None:
                all_profiles.append((test_name, test_id, profile_data_b64))

        if all_samples:
            self.create_samples(session, run, all_samples)

        if all_profiles:
            self._create_profiles(session, run, all_profiles)

    # ===================================================================
    # Bulk import (run submission)
    # ===================================================================

    def import_run(
        self,
        session: sqlalchemy.orm.Session,
        data: dict[str, Any],
    ):
        """Import a run from the v5 submission format.

        Returns the created Run.
        """
        fmt = data.get("format_version")
        if fmt != "5":
            raise ValueError(
                f"format_version is required and must be '5', got {fmt!r}"
            )

        # -- Commit (required) ----------------------------------------------
        commit_obj = self._parse_commit_data(session, data)

        # -- Run parameters -------------------------------------------------
        raw_params = data.get("run_parameters", {})
        run_parameters = self._validate_run_parameters(raw_params)

        # -- Run ------------------------------------------------------------
        run = self.create_run(
            session,
            commit=commit_obj,
            run_parameters=run_parameters,
        )

        # -- Upsert parameter keys and values --------------------------------
        self._upsert_parameter_keys_and_values(session, run_parameters)

        # -- Tests & Samples (batched) --------------------------------------
        self._parse_tests_data(session, data, run)

        return run
