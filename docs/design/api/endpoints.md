# v5 REST API: Endpoints

This document specifies all entity endpoints in the v5 REST API.

For framework, pagination, auth, and other infrastructure, see
[`infrastructure.md`](infrastructure.md).


## Commits

```
GET    /commits                      -- List (cursor-paginated, searchable)
POST   /commits                      -- Create with metadata (commit_fields)
GET    /commits/{value}              -- Detail (includes previous/next commit by ordinal)
PATCH  /commits/{value}              -- Update ordinal, tag, and/or commit_fields
DELETE /commits/{value}              -- Delete commit (cascades to runs/samples; 409 if referenced by regressions or indicators)
POST   /commits/resolve              -- Batch resolve commit strings to summaries
```

The `{value}` in the path is the commit identity string. Commits are also
created implicitly during run submission. Ordinals and tags are always NULL
on creation and assigned exclusively via PATCH (see
[D11 in db/operations.md](../db/operations.md#d11-ordinal-management)).

Filters: `search=` (case-insensitive substring match on commit string, tag, and
searchable commit fields; see D9), `param.X=Y` (only commits with at least one
run whose `run_parameters` contain `{X: Y}`; see filtering conventions in
[`infrastructure.md`](infrastructure.md)). Sort: `sort=ordinal` sorts by ordinal
ascending and excludes commits with NULL ordinals; default sort is by internal ID.

### Batch Resolve

`POST /commits/resolve` accepts a JSON body `{"commits": ["abc", "def", ...]}`
(at least one commit string) and returns each found commit's summary
in a dict keyed by commit string:

```json
{
  "results": {
    "abc": {"commit": "abc", "ordinal": 42, "fields": {"git_sha": "..."}},
    "def": {"commit": "def", "ordinal": null, "fields": {}}
  },
  "not_found": ["unknown"]
}
```

Each value in `results` has the same shape as `CommitSummarySchema`
(`{commit, ordinal, tag, fields}`). Commit strings not found in the database
are returned in a separate `not_found` list. Duplicates in the request
are deduplicated; each commit appears at most once in the response.

Auth scope: `read`. Not paginated (response is bounded by request size).


## Runs

```
GET    /runs                         -- List (cursor-paginated, filterable by param.X=Y, commit=, after=, before=)
POST   /runs                         -- Submit run (server generates UUID, returns it)
GET    /runs/{uuid}                  -- Detail
DELETE /runs/{uuid}                  -- Delete run (409 if referenced by regression indicators)
```

The UUID is a new field, generated server-side on submission. The submission
endpoint requires JSON format with `format_version '5'`. Legacy formats (v0,
v1, v2) and non-JSON payloads are rejected. There is no `on_existing_run`
parameter -- v5 always creates a new run (multiple runs per commit are
allowed). Deleting a run cascades to its samples and profiles; deletion is
refused (409) if any RegressionIndicator references the run.

Run responses include `run_parameters` (JSONB dict) as the primary metadata.

Filters on `GET /runs`: `param.X=Y` (run parameter match), `commit=` (exact
commit string), `after=` / `before=` (submitted_at time range).


## Tests

```
GET    /tests                        -- List (cursor-paginated, filterable)
```

Read-only. Tests are created implicitly via run submission.

Filters: `search=` (case-insensitive substring match on test name; see D9),
`param.X=Y` (only tests with data for runs matching these parameters),
`metric=` (only tests with non-NULL values for this metric).


## Samples

```
POST   /samples                      -- Unified sample query (cursor-paginated)
```

`POST /samples` is the unified sample query endpoint. It replaces the former
`POST /query` (time-series), `GET /runs/{uuid}/samples` (per-run), and
`GET /runs/{uuid}/tests/{name}/samples` (per-run-test) endpoints. It serves
the Graph page (time-series), Compare page (per-commit samples), Run detail
page (per-run samples), and programmatic access.

Auth scope: `read`.

**Request body** (JSON):

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

- `params`: Dict of key → value for JSONB containment filtering on
  `Run.run_parameters`. Different keys combine with AND; multiple values for
  the same key combine with OR. Optional.
- `test`: List of test names (OR/disjunction). Optional.
- `metric`: When specified, only that metric's value is returned per sample.
  When omitted, all metric values are returned as a `"metrics"` dict. Optional.
- `commit`: Exact commit string filter. Optional.
- `run`: Run UUID. When specified, returns samples for that specific run.
  Optional.
- `sort`: Comma-separated sort fields. Available: `ordinal` (by
  `Commit.ordinal`), `submitted_at` (by `Run.submitted_at`), `test` (by
  `Test.name`). Prefix with `-` for descending. When `ordinal` is in the
  sort, commits without ordinals are excluded. When omitted, results are in an
  arbitrary but stable order (by `Sample.id`); no data is excluded.
- `limit`: Max items per page. Default 5000, max 10000.
- `cursor`: Opaque pagination cursor.
- `submitted_before`, `submitted_after`: ISO datetime bounds on
  `Run.submitted_at`.

**Response**:

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

When `metric` is specified, only that metric's column appears (flat value per
item). When omitted, a `"metrics": {...}` dict contains all non-null metric
values.

`Sample.id` is always appended as a unique tiebreaker for cursor pagination.
One row per sample (array-valued metrics from submission are stored as
individual sample rows).


## Run Parameters

```
GET    /run-parameters               -- List known parameter keys (paginated)
GET    /run-parameters/{key}/values  -- List known values for a key (paginated)
```

These endpoints support the search chip UI by providing autocomplete data for
run parameter keys and values.

**`GET /run-parameters`**: Lists known parameter key names from the
`{suite}_ParameterKey` table. Supports `?search=` prefix matching. Paginated.
Response: `{"items": [{"key": "compiler"}, {"key": "os"}, ...], ...}`

**`GET /run-parameters/{key}/values`**: Lists known values for a given
parameter key from the `{suite}_ParameterValue` table. Supports `?search=`
prefix matching. Paginated with hard limit per page.
Response: `{"items": [{"value": "clang-21"}, {"value": "clang-20"}, ...], ...}`

Auth scope: `read`.


## Profiles

Profiles store hardware performance counter data at the instruction level.
Each profile is identified by a server-generated UUID. The UUID-based approach
enables stable bookmarkable identifiers for profile data endpoints, while the
listing endpoint provides the bridge from human-readable run+test coordinates
to UUIDs.

### Listing (per run)

```
GET  /runs/{uuid}/profiles              -- List profiles for a run
```

Returns an array of `{test, uuid}` objects for all profiles attached to
the given run. No pagination (bounded by tests-per-run). Auth: `read`.

### Profile Data (by UUID)

```
GET  /profiles/{uuid}                       -- Metadata + top-level counters
GET  /profiles/{uuid}/functions             -- Function list with counters
GET  /profiles/{uuid}/functions/{fn_name}   -- Disassembly + per-instruction counters
```

All three endpoints require `read` scope.

**Metadata response** (`GET /profiles/{uuid}`):
- `uuid`, `test` (test name), `run_uuid`, `counters` (dict of counter
  name -> integer value), `disassembly_format` (string)

**Functions response** (`GET /profiles/{uuid}/functions`):
- `functions`: array of `{name, counters, length}` where `counters` is
  a dict of counter name -> float (percentage), `length` is instruction
  count. Sorted by total counter value descending (hottest first).

**Function detail response** (`GET /profiles/{uuid}/functions/{fn_name}`):
- `name`, `counters` (function-level aggregates), `disassembly_format`,
  `instructions`: array of `{address, counters, text}` per instruction.
  Function names may contain special characters (C++ mangled names); the
  frontend must `encodeURIComponent` the name.

**Error handling**: If the stored profile blob is corrupt and cannot be
deserialized, the profile data endpoints return 500 with a descriptive
error message.

Profiles are submitted as base64-encoded data within the run submission
payload (see D6 in db/operations.md). No separate upload endpoint.


## Regressions

```
GET    /regressions                              -- List (cursor-paginated, filterable by state=, param.X=Y, test=, metric=, commit=)
POST   /regressions                              -- Create (accepts title, bug, notes, state, commit, indicators)
GET    /regressions/{uuid}                       -- Detail (indicators embedded)
PATCH  /regressions/{uuid}                       -- Update title, bug, notes, state, commit
DELETE /regressions/{uuid}                       -- Delete (cascades indicators)
POST   /regressions/{uuid}/indicators            -- Add indicator(s) (batch)
DELETE /regressions/{uuid}/indicators            -- Remove indicator(s) (batch, UUIDs in body)
```

Auth scopes: read=GET, triage=POST/PATCH/DELETE and indicator management.

Regressions are identified by server-generated UUID.

**Regression states** (string enum):
`detected`, `active`, `not_to_be_fixed`, `fixed`, `false_positive`

State transitions are unconstrained -- any state can be set to any other
state via PATCH.

**Create request body:**
- `title` (string, optional -- auto-generated if omitted)
- `bug` (string, optional -- URL to external bug tracker)
- `notes` (string, optional -- investigation findings, A/B results, etc.)
- `state` (string, optional -- default: `detected`)
- `commit` (string, required -- commit where the regression was introduced,
  resolved by value)
- `indicators` (array, optional -- list of `{run_uuid, test, metric}` objects;
  each run must belong to the specified commit)

**Detail response** (`GET /regressions/{uuid}`):
- `uuid`, `title`, `bug`, `notes`, `state`
- `commit` (commit identity string)
- `indicators`: list of `{uuid, run_uuid, test, metric}`

**List response items** include: `uuid`, `title`, `bug`, `state`, `commit`,
`run_count`, `test_count`. The `notes` field is included in detail
responses only, not in list.

**List filters**: `state=` (comma-separated list), `param.X=Y` (filter through
indicator → run → `run_parameters`), `test=`, `metric=`, `commit=`.

**Indicator add request** (`POST /regressions/{uuid}/indicators`):
- Array of `{run_uuid, test, metric}` objects. Each object is one indicator.
  Each run must belong to the regression's commit (409 if not).
  Duplicates (same regression+run+test+metric) are silently ignored.

**Indicator remove request** (`DELETE /regressions/{uuid}/indicators`):
- Body: `{"indicator_uuids": ["...", "..."]}`


## Trends (Dashboard Aggregation)

```
POST   /trends
```

Body (JSON): `{params, metric, last_n}`

```json
{
  "params": {"compiler": "clang-21", "os": "linux"},
  "metric": "execution_time",
  "last_n": 500
}
```

- `params`: Dict for JSONB containment filtering on `Run.run_parameters`.
  Optional. When omitted, all runs are included.
- `metric`: Required. Must have type `real`; `status` and `hash` metrics are
  rejected with 400.
- `last_n` (integer, min 1, max 10000): Limits to the most recent N commits
  by ordinal. Only commits with a non-null ordinal are included.

Returns geomean-aggregated trend data per commit. Not paginated -- the result
set is bounded by `last_n`. Each call produces one trace. Each item contains:
commit string, ordinal (always present, never null), tag, submitted_at (latest
run submission time, may be null), and geomean value.

Geomean is computed in SQL: `exp(avg(ln(positive_values)))`, skipping
zero/negative values.

Auth scope: `read`.


## Dashboard Configuration

```
GET    /dashboard                    -- Get dashboard card configuration
PUT    /dashboard                    -- Replace dashboard card configuration
```

Per-suite dashboard configuration. Each card defines a parameter query and
metric, replayed as a `/trends` call to produce a sparkline on the dashboard.

**`GET /dashboard`** response:
```json
{
  "cards": [
    {"position": 0, "params": {"compiler": "clang-21"}, "metric": "execution_time", "last_n": 500},
    {"position": 1, "params": {"compiler": "gcc-14"}, "metric": "execution_time", "last_n": 500}
  ]
}
```

**`PUT /dashboard`** replaces the full card list. Auth scope: `manage`.


## Schema and Fields

Schema definitions and metric field metadata are returned as part of the test
suite detail response (`GET /api/v5/test-suites/{name}`) rather than as
standalone endpoints. The response includes a `"schema"` object containing
`commit_fields` and `metrics` (with `name`, `type`, `display_name`, `unit`,
`unit_abbrev`, `bigger_is_better` for each).

There are no separate `/fields` or `/schema` endpoints.
