"""llms.txt endpoint: GET /llms.txt

Serves a plain-text orientation document for AI agents, following the
llms.txt convention (analogous to robots.txt). Describes what LNT is,
its domain concepts, API structure, and common workflows.

Registered as a plain Flask blueprint (not flask-smorest) so it does not
appear in the OpenAPI spec.
"""

from flask import Blueprint, make_response
import hashlib

llms_txt_bp = Blueprint('llms_txt', __name__)

LLMS_TEXT = """\
# LNT -- LLVM Nightly Test Infrastructure

LNT is a performance testing infrastructure designed for tracking software
performance over time. It collects benchmark results from test runs, detects
regressions, and provides tools for performance analysis. Originally built
for the LLVM compiler project, it can be used for any software project.

## Key Concepts

- **Test Suite**: A schema defining what metrics to collect (e.g., "nts" for
  the LLVM nightly test suite). Each suite has its own set of commits, runs,
  and tests. All data queries are scoped to a specific test suite.

- **Commit**: A named point that groups runs (e.g., a Git SHA, version number,
  or ad-hoc label). Each commit has an optional integer ordinal that places it
  in a total order for time-series analysis, and an optional human-readable tag
  (e.g., "release-18.1") for labeling. Commits may have metadata fields
  (e.g., author, commit_message) defined by the test suite schema.

- **Run**: A single test execution at a specific commit. Contains samples
  (individual test results) with metric values. Identified by UUID. Each run
  carries a ``run_parameters`` dict (JSONB) describing its environment
  (e.g., compiler, os, architecture). Run parameters are the primary metadata
  for filtering and grouping.

- **Test**: A named benchmark or test case (e.g., "SingleSource/Benchmarks/
  Dhrystone/dry"). Tests are created implicitly when runs are submitted.

- **Sample**: A single data point: one test's metric values from one run.
  Each sample records values for the metrics defined by the test suite schema
  (e.g., execution_time, compile_time, code_size).

- **Regression**: A tracked performance change, grouping one or more indicators
  (run, test, metric triples). Has a state (detected, active, fixed, etc.),
  optional title, bug link, notes, and the commit where it was introduced.

- **Run Parameters**: Key-value metadata on each run describing the build/test
  environment (e.g., ``{"compiler": "clang-21", "os": "linux"}``). Used for
  filtering across all endpoints via ``param.X=Y`` query parameters or
  ``params`` dicts in POST bodies.

## REST API (v5)

Base URL: /api/v5/
Authentication: Bearer token in Authorization header. Reads are unauthenticated
by default. Write operations require tokens with appropriate scopes
(submit, triage, manage, admin).

### Discovery

  GET /api/v5/                  List test suites and API links

### Per-Suite Endpoints (replace {ts} with suite name, e.g., "nts")

  GET    /api/v5/{ts}/commits               List commits (?search=, ?param.X=Y, ?sort=ordinal)
  GET    /api/v5/{ts}/commits/{value}       Commit detail (with prev/next)
  POST   /api/v5/{ts}/commits/resolve       Batch resolve commit strings to summaries
  GET    /api/v5/{ts}/runs                  List runs (?param.X=Y, ?commit=)
  POST   /api/v5/{ts}/runs                  Submit a run
  GET    /api/v5/{ts}/runs/{uuid}           Run detail
  DELETE /api/v5/{ts}/runs/{uuid}           Delete run (409 if referenced by indicators)
  GET    /api/v5/{ts}/runs/{uuid}/profiles  List profiles for a run
  GET    /api/v5/{ts}/profiles/{uuid}                Profile metadata + counters
  GET    /api/v5/{ts}/profiles/{uuid}/functions      Function list
  GET    /api/v5/{ts}/profiles/{uuid}/functions/{fn} Function disassembly
  GET    /api/v5/{ts}/tests                 List tests (?search=, ?param.X=Y, ?metric=)
  POST   /api/v5/{ts}/samples              Unified sample query (cursor-paginated)
  POST   /api/v5/{ts}/trends               Aggregated trend data (geomean)
  GET    /api/v5/{ts}/regressions           List regressions
  POST   /api/v5/{ts}/regressions           Create regression
  GET    /api/v5/{ts}/run-parameters        List known parameter keys
  GET    /api/v5/{ts}/run-parameters/{key}/values  List values for a key
  GET    /api/v5/{ts}/dashboard             Dashboard card configuration
  PUT    /api/v5/{ts}/dashboard             Replace dashboard cards

### Global Endpoints

  GET    /api/v5/test-suites               List all test suites
  GET    /api/v5/test-suites/{name}        Suite detail (schema + metrics)
  POST   /api/v5/test-suites               Create test suite (admin)
  GET    /api/v5/admin/api-keys            List API keys (admin)
  POST   /api/v5/admin/api-keys            Create API key (admin)
  DELETE /api/v5/admin/api-keys/{prefix}   Revoke API key (admin)

Full endpoint list including all PATCH/DELETE operations:
  /api/v5/openapi/swagger-ui

### Parameter-Based Filtering

Run parameters replace the former machine concept. Instead of filtering by
machine name, filter by parameter values:

- GET endpoints: use ``param.X=Y`` query parameters (multiple values for
  the same key = OR, different keys = AND).
  Example: ``?param.compiler=clang-21&param.os=linux``

- POST /samples and POST /trends: use ``params`` dict in the JSON body.
  Example: ``{"params": {"compiler": "clang-21", "os": "linux"}}``

### Pagination

List endpoints return cursor-paginated responses:
  { "items": [...], "cursor": { "next": "...", "previous": null } }
Pass cursor=<next> to get the next page. Use limit= to control page size.

### Common Workflows

1. Discover available data: GET /api/v5/ to list test suites, then
   GET /api/v5/{ts}/run-parameters to see what parameter keys exist.

2. Query performance history: POST /api/v5/{ts}/samples with
   { "metric": "execution_time",
     "params": {"compiler": "clang-21"},
     "test": ["test/name"],
     "sort": "ordinal" }
   to get time-series data points.

3. Submit a run: POST /api/v5/{ts}/runs with a JSON body:
   { "format_version": "5",
     "commit": "abc123",
     "run_parameters": {"compiler": "clang-21", "os": "linux"},
     "tests": [
       { "name": "test/name", "execution_time": 1.23, "compile_time": 0.45 },
       { "name": "test/name", "execution_time": [1.0, 2.0] }
     ] }
   Metric values can be scalars or arrays (arrays create multiple samples).
   Requires a token with "submit" scope.

4. Check for regressions: GET /api/v5/{ts}/regressions?state=detected
   to find new regressions. PATCH to update state, title, bug link, notes,
   or commit. Add indicators via POST /regressions/{uuid}/indicators with
   {run_uuid, test, metric} objects.

5. Inspect a specific commit: GET /api/v5/{ts}/commits/{value} returns
   the commit detail with previous/next navigation links. Use
   PATCH /api/v5/{ts}/commits/{value} to assign an ordinal for time-series
   ordering.

6. Aggregated trends: POST /api/v5/{ts}/trends with
   { "metric": "execution_time",
     "params": {"compiler": "clang-21"},
     "last_n": 500 }
   to get geomean-aggregated performance per commit for the most recent
   N commits by ordinal.
"""

_ETAG = hashlib.md5(LLMS_TEXT.encode()).hexdigest()


@llms_txt_bp.route('/llms.txt')
def llms_txt():
    """Serve the LNT orientation document for AI agents."""
    resp = make_response(LLMS_TEXT)
    resp.mimetype = 'text/plain'
    resp.charset = 'utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    resp.headers['ETag'] = _ETAG
    return resp
