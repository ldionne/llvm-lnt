# v5 Web UI: Dashboard

Page specification for the Dashboard at `/v5/`.

For the SPA architecture and routing, see [`architecture.md`](architecture.md).
Related pages: [Graph](graph.md), [Compare](compare.md).


## Dashboard -- `/v5/`

Suite-agnostic landing page providing an at-a-glance visual overview of
performance trends across all test suites.

**Layout**:
- Page header "Dashboard" with a commit range preset selector (Last 100 / Last 500 / Last 1000 buttons, default Last 500) at top-right, persisted in URL as `?range=500`.
- One section per test suite (ordered alphabetically, matching `getTestsuites()`).
- Each suite section shows a header with the suite name and a "+" button (auth-gated, requires `manage` scope) to add a new dashboard card.
- Each suite section contains a responsive grid of sparkline cards, one per configured dashboard card for that suite.

**Dashboard cards**:
- Each card is a pinned query, defined by a parameter set and a metric. Cards are server-configured per suite, stored in the `{suite}_DashboardCard` table and served via `GET /{ts}/dashboard`.
- Each card shows a small time-series chart (~300x160px) with a title derived from the parameter query and metric name (e.g., "execution_time | compiler:clang-21, os:linux").
- One trace per card -- the card replays a `POST /trends` call with its saved `params` and `metric`, using the selected `last_n` from the commit range preset.
- X-axis: sequential position (evenly spaced, no axis labels); commit string shown on hover. Y-axis: geometric mean of all test values at each commit for the parameter query + metric combination.
- Hover tooltip shows the commit string, tag (if set), and value.
- Clicking a sparkline navigates to the Graph page pre-populated with that suite, metric, and the card's parameter query as a trace.
- Loading state: placeholder skeleton while data is being fetched.
- Error state: "Failed to load" message if fetching fails.

**Adding a card** ("+" button):
- Opens an inline form with:
  - Parameter search chips: two-phase autocomplete input for building a parameter query (key prefix search via `GET /run-parameters?search=`, then value search via `GET /run-parameters/{key}/values?search=`). Multiple chips can be added.
  - Metric: dropdown selector (metrics from the suite schema, only `type === 'real'`).
  - `last_n`: defaults to the current commit range preset.
- "Save" button calls `PUT /{ts}/dashboard` with the updated card list.
- Requires `manage` scope.

**Removing a card**: Each card has a small "x" button (auth-gated, `manage` scope) that removes it from the dashboard configuration via `PUT /{ts}/dashboard`.

**Reordering**: Cards can be reordered by drag-and-drop within a suite section. The `position` field controls display order. Reordering calls `PUT /{ts}/dashboard` with the updated positions. Requires `manage` scope.

**Data flow**:
1. Suite names from `getTestsuites()` (embedded in HTML shell, no API call).
2. Per suite: `GET /{ts}/dashboard` to fetch the card configuration.
3. Per card: `POST /api/v5/{ts}/trends` with the card's `params`, `metric`, and `last_n`. The server groups all matching samples by commit and returns the geomean per group for the most recent N commits by ordinal. The frontend sorts by ordinal and assigns sequential x-positions (0, 1, 2, ...) for even spacing.
4. Sparklines render progressively as each card's data arrives.

**Geomean**: `exp(mean(ln(values)))`, skipping zero/negative values. Computed server-side in the trends endpoint. Shared utility in `utils.ts` also used by the Compare page.
