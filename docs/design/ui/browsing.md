# v5 Web UI: Browsing Pages

Page specifications for the data browsing pages: Test Suites, Run Detail,
and Commit Detail.

For the SPA architecture and routing, see [`architecture.md`](architecture.md).
Related pages: [Graph](graph.md), [Compare](compare.md).


## Test Suites -- `/v5/test-suites?suite={ts}&tab=...`

The primary entry point for browsing test suite data. Suite-agnostic page with
an internal suite picker and tabbed content.

**Suite picker**: A row of prominent card/button elements, one per test suite
(from `data-testsuites`). Clicking a card selects it (highlighted) and shows
the tab bar below. When no suite is selected, only the suite picker is visible.

**Tabs**: [Runs] [Commits] [Regressions]. Default tab is Runs.

**URL state**: `?suite={ts}&tab=runs&search=foo&offset=0` -- all state is
in query params. On mount, reads params to restore state. On changes, updates
URL via `replaceState`.

| Tab | Content | API | Search/Filter |
|-----|---------|-----|---------------|
| Runs | Run list with cursor pagination, default sort `-submitted_at` | `GET runs?param.X=Y&sort=-submitted_at&limit=25` | Parameter search chips (see Search Chip Input) |
| Commits | Commit list with cursor pagination | `GET commits?search=...&limit=25` | Search (substring match on commit, tag, searchable fields; see D9) |
| Regressions | Full regression triage interface (see below) | `GET regressions?state=...&limit=25` | State chips, metric selector, title search |

**Columns per tab:**
- **Runs**: UUID (truncated, linked), Commit (primary value), Parameters (summary of `run_parameters`), Submitted At
- **Commits**: Commit Value (primary field, linked), Tag
- **Regressions**: Title (linked to regression detail), State (badge), Commit
  (display value, linked to commit detail), Run count, Test count, Bug (external link),
  Delete button (auth-gated)

"Primary value" / "primary field" means the display-field-resolved value:
if the schema defines a commit_field with ``display: true`` and the commit
has a non-null value for that field, show it instead of the raw commit
string (see D4 in db/data-model.md).  When no display field is defined,
or the field is not populated for a given commit, fall back to the raw
commit string.  Links always use the raw commit string in the URL.

**Regressions tab details**:

The Regressions tab embeds the full regression triage UI directly in the Test
Suites page (there is no standalone Regression List page).

**Filters** (control panel above table):
- State: multi-select chips (detected, active, not_to_be_fixed, fixed,
  false_positive) -- toggleable, all deselected by default
- Metric: dropdown
- Free-text search on title (client-side, debounced)

**Actions**:
- "New Regression" button (auth-gated) -> toggles an inline create form with
  title, bug, state, commit fields. On successful creation, navigates to the
  new regression's detail page.
- Row click -> navigates to regression detail page.
- Delete: per-row button with confirmation prompt (auth-gated).

**Pagination**: Cursor-based, consistent with other list tabs.

**Detail navigation**: Clicking an item navigates to the full suite-scoped
detail page (e.g., `/v5/{ts}/runs/{uuid}`) via full page navigation. This
crosses from suite-agnostic context to suite-scoped context.

**Suite root redirect**: `/v5/{ts}/` redirects to `/v5/test-suites?suite={ts}`.

**Links out**: Run Detail, Commit Detail.


## Run Detail -- `/v5/{ts}/runs/{uuid}`

All data from a single test execution.

| Section | Shows | API Calls |
|---------|-------|-----------|
| Metadata | Commit (display value), submitted at, `run_parameters` key-value pairs | `GET runs/{uuid}` |
| Metric Selector | Drop-down to choose which metric to display (like Compare page) | `GET test-suites/{ts}` (fields from `schema.metrics`) |
| Test Filter | Text input for substring matching on test names | (client-side) |
| Samples Table | All samples + selected metric value, sorted by test name by default | `POST /samples` with `"run": "{uuid}"` |

The metric selector drop-down controls which metric column is shown in the
samples table, consistent with how the Compare page handles metric selection.

Samples are loaded progressively -- the table renders immediately with the
first page and grows as more pages arrive, with a progress indicator showing
the count. Multiple samples for the same test (repetitions) appear as separate
rows.

**Run parameters display**: The `run_parameters` JSONB is displayed as a
key-value list in the metadata section (e.g., `compiler: clang-21`,
`os: linux`, `arch: x86_64`).

**Action links**: "Compare with..." (pre-selects this run's parameters and
commit on side A) and "Delete Run" button. Clicking "Delete Run" shows a
confirmation prompt (below the action row) requiring the user to type the
first 8 characters of the run UUID. Deletion requires a valid API token with
`manage` scope. Deletion is refused (409) if regression indicators reference
the run. On success, navigates to the test suites page.

**Profile links**: Tests with profiles show a "Profile" link/icon in the
samples table. Profile presence is determined by calling
`GET /runs/{uuid}/profiles` (fetched once on page load, cached). The link
navigates to `/v5/profiles?suite_a={ts}&run_a={uuid}&test_a={test}`.

**Links out**: Commit Detail, Graph (test pre-filled),
Profiles (pre-populated with run + test), Compare (side A pre-selected).


## Commit Detail -- `/v5/{ts}/commits/{value}`

The "what happened at this commit?" page. Key investigation page for developers.

- **Heading** shows the raw commit string (not the display value), since
  this page identifies a specific commit by its raw identity.

- Commit field values displayed prominently
- **Tag display + editing**: Show the commit's tag (if set) prominently (e.g., "Tag: release-18.1"). An inline edit button allows setting or clearing the tag via ``PATCH /commits/{value}``. Editing requires an API token with `manage` scope (from Settings); show an auth error if the token is missing or insufficient. The tag also appears in the display value throughout the UI as ``<display_value> (tag)``.
- **Navigation**: Prev/Next buttons (using the API's `previous_commit`/`next_commit` from the commit detail response)
- **Summary**: N runs
- **Runs table**: Columns: run UUID (link to Run Detail), parameters summary (`run_parameters` key-value pairs), submitted at
- API: `GET commits/{value}`, `PATCH commits/{value}` (tag editing), `GET runs?commit={value}`
- **Links out**: Run Detail, Regression Detail

**Regressions at this commit**: Below the runs table, a section listing
regressions where `commit` matches this commit's value. Each links to its
regression detail page.


## Regression Detail -- `/v5/{ts}/regressions/{uuid}`

Investigation and management page for a single regression.

**Page header**: Shows "Regression: {title}" when a title is set, or
"Regression: {uuid_short}" as fallback. Updates dynamically when the title is
edited.

**Header section** (editable fields):
- Title: inline-editable text. Enter key saves.
- State: dropdown selector (detected, active, not_to_be_fixed, fixed, false_positive)
- Bug: URL input (opens in new tab when set). Enter key saves.
- Commit: display value shown (linked to commit detail page). Combobox with API search for editing (shows display values in dropdown). Required (NOT NULL).
- Notes: text display with Edit button. Edit mode shows textarea + Save/Cancel. Ctrl/Cmd+Enter saves. Display preserves line breaks (pre-wrap).

**Delete regression**: Button with type-to-confirm prompt. Requires `triage`
scope. On success, navigates to the regressions tab.

**Add indicators panel**:
- Metric: dropdown selector
- Run: search chip input to narrow to a specific run (parameter chips + commit selection). Shows feedback like "327 runs -> add `arch:aarch64` -> 12 runs -> add `commit:abc123` -> 1 run." Once a single run is identified, it is selected.
- Tests: checkbox list with filter input (multi-select, shift+click range), filtered by selected run and metric
- Preview: "This will add N indicators" (selected tests count)
- "Add" button creates all (run x test x metric) indicator combinations
- Duplicates (same run+test+metric already on this regression) are silently ignored

**Indicators table**:
- Heading: "Indicators (X tests across Y runs across Z metrics)" --
  unique counts computed from the indicators, excluding null run/test
  values (from deleted entities). Shows plain "Indicators" when empty.
  When a filter is active: "Indicators (showing N of X tests across ...)".
- Filter: text input above the table for substring matching on run
  parameters, test name, or metric (OR logic, case-insensitive). Filters the
  table rows client-side. Not shown when there are no indicators.
- Columns: select checkbox, Run (short UUID, linked to Run Detail), Test, Metric, "View on graph" link, remove button (x)
- Select-all checkbox in header (with indeterminate state for partial selection)
- Shift+click range selection on checkboxes
- Batch "Remove selected" button
- "View on graph" link per indicator: opens Graph page pre-populated with the indicator's run parameters, test, metric, and the regression's commit as context

Auth: requires `triage` scope for all modifications.
