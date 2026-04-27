# v5 Design: Removing the Machine Concept

This document captures the rationale and full specification for removing the Machine
concept from LNT v5 and replacing it with flat run parameters. It is a working
document — after the normative design docs are updated, this document is no longer
the authoritative reference.


## Motivation

The Machine concept is the weakest part of the v5 data model. In modern CI,
"machines" are ephemeral — the same logical configuration might run on different
physical hosts, and the same host might run different configurations. What users
actually care about is the *configuration*: compiler, OS, architecture, build
flags. The machine name is just a proxy for a set of properties.

This redesign makes that relationship explicit. Instead of a Machine entity that
groups runs and carries metadata, runs carry their own metadata as flat key-value
parameters. Filtering, graphing, and comparing are done by querying runs that
match a set of parameter constraints.

The Machine concept currently serves three roles:

1. **Identity grouping** — "these runs belong to machine X." Replaced by
   parameter-based queries: "these runs have `compiler=clang-21` and
   `os=linux`."
2. **Filter dimension** — "give me time-series data for machine X." Replaced by
   JSONB containment queries on `run_parameters`.
3. **Metadata carrier** — `machine_fields` and `parameters` JSONB. Absorbed into
   `run_parameters` on the Run table.

Named or saved queries (e.g., bookmarking a frequently-used parameter combination)
are a convenience feature, not a core primitive. The core primitive is "match runs
by parameters."


## Schema and Data Model Changes

### Tables Removed

- **`{suite}_Machine`** — removed entirely. All machine-related metadata moves
  into `run_parameters` on the Run table.
- **`machine_fields`** in the schema YAML definition — removed. Parameters are
  ad-hoc JSONB, not schema-defined typed columns.

### Tables Modified

**`{suite}_Run`**:
- `machine_id` FK dropped (column and index removed).
- `run_parameters` JSONB becomes the primary metadata carrier.
- GIN index (`jsonb_path_ops`) added on `run_parameters` for containment queries.
- Compound index `(machine_id, commit_id)` removed.

**`{suite}_RegressionIndicator`**:
- `machine_id` FK replaced by `run_id` FK → Run.
- Unique constraint changes from `(regression_id, machine_id, test_id, metric)` to
  `(regression_id, run_id, test_id, metric)`.
- No `ondelete` CASCADE on `run_id` — run deletion is refused if indicators
  reference it (see invariants below).

**`{suite}_Regression`**:
- `commit_id` becomes NOT NULL (was nullable).
- Justification: regressions now capture specific evidence at a known commit. A
  regression is created from a comparison at a specific commit, and all its
  indicator runs must be at that commit. The previous workflow of "detect first,
  identify the commit later" is replaced by "create the regression from a
  comparison at the commit where the change was observed."
- The `has_commit` filter on the list endpoint is removed.

### Tables Added

**`{suite}_ParameterKey`** — tracks known run parameter key names for autocomplete.

| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| key | String(256) | unique, not null |

Upserted via `INSERT ... ON CONFLICT DO NOTHING` during run submission. One row
per distinct parameter key ever seen.

**`{suite}_ParameterValue`** — tracks known (key, value) pairs for value
autocomplete.

| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| key_id | Integer FK → ParameterKey | not null |
| value | String(256) | not null |

- Unique constraint on `(key_id, value)`.
- Upserted during run submission alongside ParameterKey.

**`{suite}_DashboardCard`** — per-suite dashboard configuration.

| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| position | Integer | not null |
| params | JSONB | not null, default `{}` |
| metric | String(256) | not null |
| last_n | Integer | not null, default 500 |

- Each card stores a parameter query and metric, replayed as a `/trends` call.
- `position` controls display order.

### Schema Format Changes

The `machine_fields` section is removed from the YAML schema definition. The
schema now contains only `name`, `metrics`, and `commit_fields`. There is no
replacement section for run parameters — they are fully ad-hoc JSONB, not
schema-defined.

### Key Invariants

- **Regression-commit consistency**: All indicator runs on a regression must share
  the regression's commit. Enforced at the API layer; violations return 409.
  `PATCH` on a regression's commit re-validates existing indicators.
- **Parameter key format**: Must match `[a-zA-Z0-9_]+`. Keys that conflict with
  query parameter names (`commit`, `test`, `metric`, `sort`, `cursor`, `limit`,
  `run`, `search`) are rejected at submission time.
- **Value type coercion**: All parameter values are coerced to strings at
  ingestion. JSONB stores them as string values, ensuring `@>` containment
  queries work consistently with string query parameters.
- **Run deletion guard**: Deleting a run is refused (409) if regression indicators
  reference it. Indicators must be removed first.
- **Commit deletion guard**: In addition to checking `Regression.commit_id`
  (existing), must also check for indicators referencing runs at that commit.
- **Parameter filter semantics**: Different `param.` keys combine with AND;
  multiple values for the same key combine with OR.
- **Empty parameters**: A run with empty `run_parameters` (`{}`) is valid.


## Submission and Operations Changes

### Submission Format

Runs are submitted as JSON via `POST /api/v5/{suite}/runs`:

```json
{
  "format_version": "5",
  "commit": "abc123def456",
  "commit_fields": {
    "git_sha": "abc123def456789...",
    "author": "Jane Doe",
    "commit_message": "Fix vectorizer regression"
  },
  "run_parameters": {
    "compiler": "clang-21",
    "os": "linux",
    "arch": "x86_64",
    "build_config": "Release"
  },
  "tests": [
    {
      "name": "test.suite/benchmark",
      "execution_time": 1.23,
      "compile_time": 0.45,
      "profile": "<base64-encoded profile data>"
    }
  ]
}
```

- `format_version`: Required, must be `"5"`.
- `commit`: Required string. Identifies which commit this run belongs to.
- `commit_fields`: Optional. Same as before (see D7).
- `run_parameters`: Optional (defaults to `{}`). Flat key-value metadata for the
  run. Keys must match `[a-zA-Z0-9_]+` and must not use reserved names. Values
  are coerced to strings.
- `tests`: Required. Same as before.

The `machine` object is removed. The `on_machine_conflict` query parameter is
removed.

On submission, the server upserts all parameter keys into `{suite}_ParameterKey`
and all (key, value) pairs into `{suite}_ParameterValue` using
`INSERT ... ON CONFLICT DO NOTHING`.

### Operations Changes

- **Search (D9)**: `GET /machines?search=` is removed. Run parameter discovery
  uses the new `GET /run-parameters` endpoint.
- **Time-series queries (D10)**: The primary query pattern changes from "metric
  values for (machine, test, metric) ordered by ordinal" to "metric values for
  (parameter set, test, metric) ordered by ordinal." The `machine_id` filter is
  replaced by JSONB containment on `run_parameters`.
- **Concurrent submission (D14)**: The `get_or_create_machine` pattern is removed.
  ParameterKey/Value upserts use `INSERT ... ON CONFLICT DO NOTHING`, providing
  the same concurrency safety.


## API Endpoint Changes

### Removed Endpoints

All `/machines` endpoints (6 total): list, create, detail, patch, delete,
runs-for-machine.

### Replaced Endpoints

| Old | New |
|-----|-----|
| `GET /runs/{uuid}/samples` | `POST /samples` with `"run": "UUID"` |
| `GET /runs/{uuid}/tests/{name}/samples` | `POST /samples` with `"run": "UUID", "test": ["name"]` |
| `POST /query` | `POST /samples` with parameter/test/metric/sort filters |

`POST /samples` is the unified sample query endpoint. It serves the Graph page
(time-series), Compare page (per-commit samples), Run detail page (per-run
samples), and programmatic access.

### New Endpoints

**`POST /samples`** — see full specification below.

**`GET /run-parameters`** — list known parameter keys for a test suite.

- Supports `?search=` prefix matching.
- Paginated.
- Response: `{"items": [{"key": "compiler"}, {"key": "os"}, ...], ...}`
- Auth: `read`.

**`GET /run-parameters/{key}/values`** — list known values for a parameter key.

- Supports `?search=` prefix matching.
- Paginated with hard limit per page.
- Response: `{"items": [{"value": "clang-21"}, {"value": "clang-20"}, ...], ...}`
- Auth: `read`.

**`GET /{ts}/dashboard`** — returns dashboard card configuration for a suite.

- Response: `{"cards": [{"position": 0, "params": {...}, "metric": "...", "last_n": 500}, ...]}`
- Auth: `read`.

**`PUT /{ts}/dashboard`** — replace dashboard card configuration.

- Auth: `manage`.
- Body: `{"cards": [...]}`

### Modified Endpoints

**`POST /runs`** — submission format changes (no `machine` object, no
`on_machine_conflict` query parameter).

**`GET /runs`** — `?machine=` filter replaced by `?param.X=Y`. Response drops
`machine` field; `run_parameters` is the metadata.

**`GET /runs/{uuid}`** — response drops `machine`, shows `run_parameters`.

**`DELETE /runs/{uuid}`** — refuses (409) if regression indicators reference the
run.

**`GET /commits`** — `?machine=` filter replaced by `?param.X=Y`. Implemented as
`EXISTS (SELECT 1 FROM Run WHERE commit_id = Commit.id AND run_parameters @> ...)`.

**`GET /tests`** — `?machine=` filter replaced by `?param.X=Y`.

**`POST /trends`** — `machine` list replaced by `params` dict. Groups by commit
only (no machine dimension). See full specification below.

**Regression endpoints**:
- Indicators change from `{machine, test, metric}` to `{run_uuid, test, metric}`.
- `?machine=` filter replaced by `?param.X=Y` (through indicator → run →
  `run_parameters`).
- `machine_count` in list response replaced by `run_count`.
- Adding an indicator whose run's commit does not match the regression's commit
  returns 409.

### Filtering Conventions

- **GET endpoints** (browsing): `?param.X=Y` query parameters.
- **POST endpoints** (querying): `"params": {...}` in JSON body.
- `reject_unknown_params` updated to allow the `param.*` prefix on GET endpoints.
- Multiple values for the same key = OR (e.g., `?param.os=linux&param.os=darwin`
  matches runs with either OS).
- Different keys = AND (e.g., `?param.os=linux&param.arch=x86_64` matches runs
  with both).
- No negation or key-existence filters (deferred to future work).

### `POST /samples` Specification

Request body:

```json
{
  "params": {"compiler": "clang-21", "os": "linux"},
  "test": ["benchmark/foo", "benchmark/bar"],
  "metric": "execution_time",
  "commit": "abc123",
  "run": null,
  "sort": "ordinal",
  "limit": 5000,
  "cursor": "...",
  "submitted_before": "2026-04-01T00:00:00Z",
  "submitted_after": "2026-03-01T00:00:00Z"
}
```

- `params`: Dict of key → value for JSONB containment filtering. Optional.
- `test`: List of test names (OR/disjunction). Optional.
- `metric`: When specified, only that metric's column is returned. When omitted,
  all metrics are returned as a dict.
- `commit`: Exact commit filter. Optional.
- `run`: Run UUID. When specified, returns samples for that specific run. Optional.
- `sort`: `ordinal`, `-ordinal`, `submitted_at`, `-submitted_at`, `test`, `-test`.
  When omitted, results are in an arbitrary but stable order (by `Sample.id`).
  When `ordinal` is in the sort, commits without ordinals are excluded.
- `limit`: Max items per page. Default 5000, max 10000.
- `cursor`: Opaque pagination cursor.
- `submitted_before`, `submitted_after`: ISO datetime bounds on `Run.submitted_at`.

Response:

```json
{
  "items": [
    {
      "test": "benchmark/foo",
      "execution_time": 1.23,
      "run_uuid": "...",
      "commit": "abc123",
      "ordinal": 42,
      "tag": "v1.0",
      "submitted_at": "2026-04-15T14:30:00Z"
    }
  ],
  "cursor": {"next": "..."}
}
```

When `metric` is specified, only that metric's column appears. When omitted, all
metrics appear as a `"metrics": {...}` dict.

`Sample.id` is always appended as a unique tiebreaker for cursor pagination.

Auth: `read`.

### `POST /trends` Specification

Request body:

```json
{
  "params": {"compiler": "clang-21", "os": "linux"},
  "metric": "execution_time",
  "last_n": 500
}
```

- `params`: Dict for JSONB containment filtering. Replaces the `machine` list.
- `metric`: Required. Must have type `real`.
- `last_n`: Limits to most recent N commits by ordinal.

Response:

```json
{
  "items": [
    {"commit": "abc", "ordinal": 1, "tag": "v1.0", "value": 1.456, "submitted_at": "..."},
    {"commit": "def", "ordinal": 2, "tag": null, "value": 1.478, "submitted_at": "..."}
  ]
}
```

Geomean only: `EXP(AVG(LN(positive_values)))`. Groups by commit (no machine
dimension). One call produces one trace. Not paginated — bounded by `last_n`.

Auth: `read`.


## Frontend / UI Changes

### Pages Removed

- **Machine Detail** (`/v5/{ts}/machines/{name}`) — removed entirely.
- **Machines tab** in Test Suites — removed.

### Tabs Removed

- **Recent Activity tab** — absorbed by the Runs tab sorted by `-submitted_at`.

### New Component: Search Chip Input

A two-phase autocomplete input used across Compare, Graph, Profiles, and the
Runs tab:

1. User types a key prefix (e.g., `comp`). Server suggests matching keys from
   `GET /run-parameters?search=comp` (returns `compiler`, `compile_flags`, ...).
   Builtin keys (`commit`) are also suggested.
2. User selects a key (e.g., `compiler:`), presses Tab. Server suggests matching
   values from `GET /run-parameters/compiler/values?search=...`.
3. User selects or types a value. A chip like `(compiler: clang-21)` is created.
4. User can add more chips. Press Enter to execute the query.

### Pages Redesigned

**Compare**: The suite → machine → commit → run cascade is replaced by a search
chip UI per side. Commits are scoped by `GET /commits?param.X=Y` instead of
`?machine=M`. A "no commit selected" hint is shown when the query spans multiple
commits.

**Graph**: Machine chip multi-select is replaced by search chip traces. Each
trace is defined by a parameter query. The trace legend displays the parameter
query. Baselines are encoded as `{suite, params, commit}` instead of
`{suite, machine, commit}`.

**Dashboard**: Hardcoded per-machine sparklines are replaced by pinned query
cards. Per-suite, server-configured (Manage scope). A "+" button adds a new card.
Each card replays a `/trends` call with its saved params and metric.

**Regression Detail**: The indicator table shows `(run_uuid, test, metric)`
instead of `(machine, test, metric)`. The "Add indicator" panel changes from
machine × test checkboxes to run selection + test checkboxes.

**Profiles**: Machine picker is replaced by parameter search + commit selection,
progressively narrowing to a single run. The UI shows feedback like "327 runs →
add `arch:aarch64` → 12 runs → add `commit:abc123` → 1 run."

**Commit Detail**: Machine filter and column are removed. Runs are shown with a
summary of `run_parameters`. The "N runs across M machines" summary becomes
"N runs."

**Test Suites Runs tab**: Machine name filter becomes parameter chip search.
Default sort is `-submitted_at`, absorbing the Recent Activity functionality.

**Run Detail**: Machine link is removed. `run_parameters` is displayed as
key-value metadata. "Compare with..." encodes parameters instead of machine.
After-delete navigation goes to the test-suites page.


## Performance Considerations

- **GIN index** (`jsonb_path_ops`) on `run_parameters` is required for
  containment queries. Expression indexes on frequently-queried keys can be added
  later as an optimization.
- **Time-series hot path** changes from B-tree integer FK lookup to GIN
  containment. This is expected to be slower and needs benchmarking with
  realistic data volumes. If unacceptable, expression indexes on hot keys provide
  B-tree performance for known filter patterns.
- **ParameterKey/Value upserts**: `INSERT ... ON CONFLICT DO NOTHING` causes
  short-lived row locks on hot keys during concurrent submissions. Acceptable for
  expected workloads.
- **Dashboard load**: Each card fires a separate `/trends` query.
  Caching or materialized views can be added later if needed.
- **Existing performance TODOs** (dump_response validation, OrderedDict overhead,
  etc.) become more important with the high default page sizes on `/samples`.


## Open Questions / Future Work

- **Negation filters** (`?param.compiler=!gcc`) — deferred.
- **Key-existence filters** ("runs that HAVE parameter X") — deferred.
- **Expression indexes** on hot parameter keys — add when benchmarking shows need.
- **`lnt submit` CLI** — must be updated to new submission format.
- **`llms.txt`** — must be rewritten to reflect the parameter-based model.
- **Search chip component** detailed design — iterative during frontend
  implementation.
- **URL encoding** for parameter queries — format TBD during implementation.
- **Dashboard card CRUD** detailed API schema — finalize during implementation.


## Migration Note

No automated migration is provided. There are no existing v5 production
instances. The `machine_fields` section in schema YAML is removed entirely. The
`v5_schema.schema_json` column will no longer contain `machine_fields`. D12
(v4 → v5 migration tool) is not yet implemented and will be designed against the
new machine-free model.
