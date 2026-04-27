# v5 Web UI: Graph Page

Page specification for the Graph (time series) page at `/v5/graph`.

For the SPA architecture and routing, see [`architecture.md`](architecture.md).
Related pages: [Compare](compare.md), [Browsing Pages](browsing.md).


## Graph (Time Series) -- `/v5/graph?suite={ts}&metric={f}&trace=...`

The primary performance-over-time visualization. Replaces v4's graph page. This
page is suite-agnostic -- the suite is a query parameter, not a path segment.

- **Suite selector**: A required dropdown at the top of the page, populated from the `data-testsuites` HTML attribute. All other controls (traces, metric, test filter, aggregation, baselines) are disabled until a suite is selected. Changing the suite clears all traces, all caches, and the chart. When the page is loaded with `suite=` in the URL, the dropdown is pre-selected.

- **Search chip trace input**: The trace selector is a search chip input. Each trace is defined by a parameter query -- a set of key-value chips that filter runs by `run_parameters`. The user builds a parameter query using the two-phase autocomplete (key prefix via `GET /run-parameters?search=`, then value via `GET /run-parameters/{key}/values?search=`). Pressing Enter after adding one or more chips creates a new trace. Each trace appears as a removable label showing its parameter query (e.g., `compiler:clang-21, os:linux`). Multiple traces can be added to overlay their data on the same chart. Removing the last trace clears the chart. The metric selector is shared across all traces -- the same metric is plotted for every trace.

- **Input validation**: Commit comboboxes (used in baselines) show a red halo (`.combobox-invalid` -- red border + box-shadow) whenever the suggestion dropdown is empty, meaning no commit matches the typed text. Acceptance (Enter key, blur/change) is blocked while the halo is showing. The halo updates in real-time on every keystroke. Clicking a dropdown suggestion always clears the halo and accepts the value. For commit comboboxes, acceptance via Enter or blur additionally requires an exact match against available commit values -- a partial substring match (e.g. typing "789" when the commit is "566789") is rejected with the red halo even though suggestions are visible. All comboboxes support ArrowDown/ArrowUp keyboard navigation through suggestions, with Enter to select the focused item.

- **Explicit test selection**: There is no "Plot" button or auto-plot. When at least one trace and a metric are selected, the test table is populated with ALL matching tests (no cap). **Nothing is plotted by default** -- the chart starts empty with the x-axis scaffold. The user explicitly selects which tests to plot by clicking rows in the test table. Data is fetched on-demand when tests are selected. The metric selector initially shows a "-- Select metric --" placeholder (no metric pre-selected), consistent with the Compare page.

- **Multi-trace naming and symbols**: Each trace is named `{test name} - {parameter query}` (test name first for natural sorting). Traces are visually distinguished by marker symbols: the first trace uses circles (default), the second triangles, then squares, diamonds, etc. Colors represent test identity, assigned by the test's position in the alphabetically sorted full test list (not just the selected subset). This ensures stable colors -- adding or removing a selection does not shuffle existing colors. The same test on different traces shares the same color but has a different marker shape.

- **Test filter**: A text filter (like the Compare page) that controls which tests appear in the test table. The filter matches on **test name only** (not parameter query) via case-insensitive substring. Changing the filter prunes selected tests that no longer match -- their traces are removed from the chart. Clearing the filter restores the full test list (previously selected tests remain selected if they match).

- **X-axis is always commit** (not date -- commits are not necessarily correlated to dates).
  When the schema defines a commit_field with ``display: true``, the X-axis
  labels, hover tooltips, and baseline chip labels show the display value
  (e.g. short SHA) instead of the raw commit string.  When no display field
  is defined or a commit's display field is not populated, the raw commit
  string is shown.

- Plotly line chart: metric value vs commit, one trace per matching test

- **Aggregation controls** (consistent with Compare page):
  - Run aggregation: how to combine multiple runs at the same commit (median/mean/min/max)
  - Sample aggregation: how to combine multiple samples within a run (median/mean/min/max)


### Lazy Loading with Progressive Rendering

Data is fetched on-demand when tests are selected (not eagerly on discovery).
For each selected test, data is fetched via `POST /samples` with the trace's
parameter query, OR'd test names, the selected metric, and `sort: "ordinal"`,
rendered incrementally. When shift-clicking to select a range, the batch
of tests is fetched in a single query. The chart progressively fills in data as
pages arrive via cursor-based pagination. This avoids blocking the UI on large
datasets.


### X-axis Scaffolding

To prevent the x-axis from resizing/shifting as lazy-loaded pages arrive, the
graph page pre-fetches the complete list of commit values for each trace's
parameter query via paginated calls to `GET commits?param.X=Y&sort=ordinal`.
This returns commits in ordinal order, excluding commits without ordinals
(which have no meaningful position in a time series). When multiple traces
are active, the scaffold is the **union** of all traces' commit values,
sorted by ordinal, so the x-axis spans the full range across all traces.
Traces naturally have gaps where their parameter query has no data at a given
commit. Each trace's scaffold is fetched and cached independently; the union
is recomputed when traces are added or removed. If a scaffold fetch fails for
one trace, that trace's commits are simply not included in the union --
the chart still works.


### Incremental Chart Updates

The chart component exposes a `ChartHandle` API (via `createTimeSeriesChart`)
that supports incremental updates through `Plotly.react()` -- the chart is
updated in-place as new pages of data arrive, rather than being destroyed and
re-created.


### Zoom Preservation During Progressive Loading

If the user zooms into the chart while data is still loading, the zoom is
preserved across incremental updates. The x-axis range is always preserved (it
was established by the scaffold or by user zoom). The y-axis range is preserved
only when the user has explicitly zoomed; otherwise, it auto-ranges to
accommodate new data as it arrives. Double-clicking the chart resets the zoom to
the full range as usual.


### Test Selection Table

Below the chart, a table lists ALL tests matching the current filter, sorted
alphabetically by test name. One row per test name (not per test x trace
combination -- selecting a test plots it on all active traces). The table is
part of the normal page flow (no scrollable container). A message line above
the rows shows counts (e.g., "3 of 1200 tests selected" or "3 of 1200 tests
selected, loading..."). Each row has: a checkbox cell (checked =
selected/plotted), a symbol cell (colored marker character (circle/triangle/square) only when
selected, empty otherwise), and the test name. The test filter narrows the
table; tests that no longer match are pruned from the selection.

**Filter performance**: Typing in the test filter must feel instant even with
thousands of tests. Non-matching rows are hidden immediately; the chart updates
asynchronously.


### Selection Interactions

A header "check all" checkbox in the table header selects or deselects all
visible tests (tri-state: unchecked, indeterminate when some selected, checked
when all selected). Clicking a row toggles its selection (and triggers data
fetch if selecting). Shift-clicking selects a contiguous range from the
last-clicked row (additive -- adds to existing selection). Double-clicking
isolates that test (deselects all others); double-clicking the sole selected
test restores all (selects every visible test). Selected tests with data still
loading show a loading indicator. Plotly's built-in legend is disabled; the
table replaces it. Bidirectional hover highlighting: hovering a table row
highlights the corresponding chart trace(s); hovering a chart trace highlights
the table row. Selected tests are NOT persisted in the URL (test names can be
very long); the filter, suite, traces, metric, aggregation, and baselines
remain in the URL.


### Client-Side Caching and State Persistence

Test names, data points, scaffolds, and baseline data are cached locally. Test
names are fetched once per trace/metric combination (all names, no
server-side filter) and filtered client-side. Changing the test filter or
aggregation mode re-renders instantly from cache without any additional API
calls. Adding a second trace starts its own fetch pipeline while the first
trace's data is already displayed. The cache, the selected test set, and the
matching test list are all preserved across page unmount/remount, so navigating
away and pressing browser back renders the previous selection and chart
instantly from cache. All caches and selections are cleared on suite change.


### Baselines

Users can overlay one or more baselines as horizontal dashed lines on the
chart. Each baseline is a (suite, params, commit) tuple, allowing cross-suite
comparisons. The selector is an expandable panel with cascading inputs:
Suite (populated from `data-testsuites`) -> Parameters (search chip input for
building a parameter query) -> Commit (combobox populated from
`GET commits?param.X=Y` scoped to the selected parameter query). Added
baselines appear as removable chips labeled
`{suite}/{param_summary}/{display_value}`, where `param_summary` is a
compact representation of the parameter query (e.g., `compiler:clang-21,os:linux`)
and `display_value` is the commit's display value (e.g. short SHA with tag)
when a `commit_field` with `display: true` is defined, otherwise the raw
commit string. Display values for baseline commits are resolved via
`POST /commits/resolve` so they display correctly when baselines are loaded
from the URL. The "+" button uses `align-self: flex-start` so it does not
stretch to the width of the chips. Baseline data is fetched from the
baseline's suite via `POST /api/v5/{suite}/samples` with
`{params, metric, commit, test}` in the JSON body. Each baseline renders as
a horizontal dashed line per test trace, spanning the full chart width, colored
to match the corresponding test's main trace. The baseline's Y value for each
test is computed using the same run aggregation function as the main trace
(e.g., median of all runs at that commit), so the dashed line aligns exactly
with the trace point at that commit. Hovering a dashed line shows a tooltip
with: the baseline suite, parameter query, commit value, tag (if set), test
name, and metric value. Baselines are encoded in the URL query string for
shareability (e.g.,
`&baseline=nts::compiler%3Dclang-21::abc123&baseline=other_suite::os%3Dlinux::def456`).
Baseline data is fetched asynchronously after the first render, so it does not
block initial chart display.


### Concurrent Background Fetches

Each trace x metric fetch uses its own AbortController, so navigating away or
removing a trace cancels its in-flight requests cleanly without affecting
other traces' fetches.


### Hover Behavior

Hover a data point: tooltip showing test name, parameter query, commit value,
aggregated metric value, run count. Hover distance is reduced
(`hoverdistance: 5`, less sticky tooltips) so the tooltip only appears when the
cursor is close to a data point. When hovering over an aggregated point that
represents multiple runs, the individual pre-aggregation values are shown as a
scatter of markers at the same x-position, in the same trace color but faded
(opacity 0.3). This scatter is computed lazily via a callback and displayed as
a temporary Plotly trace that is added on hover and removed on unhover.


### Empty State

When no traces match the current filter/settings, the chart displays a Plotly
annotation overlay ("No data to plot") centered on the chart area, preserving
the x-axis scaffold so the user can see the commit range.


### API Calls

- `POST /samples` with JSON body `{params, metric, test, sort, limit, cursor}` (one fetch pipeline per trace, targeted to discovered tests via multi-value `test`)
- `GET tests?param.X=Y&metric=...&search=...` (test name discovery)
- `GET commits?param.X=Y&sort=ordinal` (x-axis scaffold, per trace)
- `GET commits` (tags for baseline suggestions)
- `GET run-parameters?search=...` (parameter key autocomplete)
- `GET run-parameters/{key}/values?search=...` (parameter value autocomplete)
- `GET test-suites/{ts}` (fields/metrics)


### URL State

`?suite={ts}&trace={params_encoded}&trace={params_encoded2}&metric={name}&test_filter={text}&run_agg={fn}&sample_agg={fn}&baseline={suite}::{params_encoded}::{commit}&baseline={suite2}::{params_encoded2}::{commit2}`

The `trace` parameter is repeated for each trace (each value is an encoded
parameter query); the `baseline` parameter is repeated for each baseline.
Selected tests are NOT included in the URL (names can be very long); they are
ephemeral page state preserved across SPA navigation but lost on page reload.

**Links out**: Compare, Regression Detail


### Regression Annotations

A dropdown toggle "Regressions: Off | Active | All" (default Off) in the
controls panel. When enabled, vertical dashed lines are drawn at the
regression's commit position for regressions with indicators matching the
current graph's test/parameter query/metric. Lines are color-coded by state
(red=active, yellow=detected, gray=resolved). Hover shows the regression title
and affected tests; click navigates to the regression detail page.
