// pages/test-suites.ts — Test Suites page with suite picker and browsing tabs.
// Suite-agnostic — served at /v5/test-suites.

import type { PageModule, RouteParams } from '../router';
import type { RunInfo, CommitSummary } from '../types';
import type { CursorPageResult } from '../api';
import { getTestsuites } from '../router';
import { getRunsPage, getCommitsPage, getTestSuiteInfoCached } from '../api';
import type { Column } from '../components/data-table';
import { el, formatTime, truncate, debounce, commitDisplayValue, resolveDisplayMap } from '../utils';
import { renderDataTable } from '../components/data-table';
import { renderPagination } from '../components/pagination';
import { renderRegressionTab } from './regression-list';
import { formatParamQuery } from '../types';

const PAGE_SIZE = 25;

type TabId = 'runs' | 'commits' | 'regressions';

let tabController: AbortController | null = null;
let tabCleanupFns: (() => void)[] = [];

/** Build a full href to a suite-scoped detail page (full page navigation). */
function suiteHref(suite: string, path: string): string {
  // lnt_url_base is set as a global by the HTML template
  const base = typeof (globalThis as Record<string, unknown>).lnt_url_base === 'string'
    ? (globalThis as Record<string, unknown>).lnt_url_base as string
    : '';
  return `${base}/v5/${encodeURIComponent(suite)}${path}`;
}

/** Create a plain <a> link for full page navigation (not SPA). */
function detailLink(text: string, suite: string, path: string): HTMLAnchorElement {
  return el('a', { href: suiteHref(suite, path) }, text) as HTMLAnchorElement;
}

export const testSuitesPage: PageModule = {
  mount(container: HTMLElement, _params: RouteParams): void {
    // Abort any previous tab load
    if (tabController) tabController.abort();

    const suites = getTestsuites();

    // Read initial state from URL query params
    const urlParams = new URLSearchParams(window.location.search);
    let selectedSuite = urlParams.get('suite') || '';
    let activeTab: TabId = (urlParams.get('tab') as TabId) || 'runs';
    let currentSearch = urlParams.get('search') || '';
    let commitFields: Array<{ name: string; display?: boolean }> = [];

    container.append(el('h2', { class: 'page-header' }, 'Test Suites'));

    // --- Suite picker ---
    const picker = el('div', { class: 'suite-picker' });
    const cardMap = new Map<string, HTMLElement>();

    for (const name of suites) {
      const card = el('button', { class: 'suite-card' }, name);
      if (name === selectedSuite) card.classList.add('suite-card-active');
      card.addEventListener('click', () => {
        if (selectedSuite === name) return;
        selectSuite(name);
      });
      cardMap.set(name, card);
      picker.append(card);
    }

    if (suites.length === 0) {
      picker.append(el('p', {}, 'No test suites available.'));
    }
    container.append(picker);

    // --- Tab bar (hidden until suite selected) ---
    const tabBar = el('div', { class: 'v5-tab-bar', style: selectedSuite ? '' : 'display:none' });
    const tabDefs: Array<{ id: TabId; label: string }> = [
      { id: 'runs', label: 'Runs' },
      { id: 'commits', label: 'Commits' },
      { id: 'regressions', label: 'Regressions' },
    ];
    const tabButtons: HTMLElement[] = [];
    for (const tab of tabDefs) {
      const btn = el('button', {
        class: `v5-tab${tab.id === activeTab ? ' v5-tab-active' : ''}`,
        'data-tab': tab.id,
      }, tab.label);
      btn.addEventListener('click', () => {
        if (activeTab === tab.id) return;
        activeTab = tab.id;
        currentSearch = '';
        activateTab(tab.id);
        syncUrl();
        loadTabContent();
      });
      tabButtons.push(btn);
      tabBar.append(btn);
    }
    container.append(tabBar);

    // --- Tab content area ---
    const tabContent = el('div', { class: 'v5-tab-content' });
    container.append(tabContent);

    function activateTab(tabId: TabId): void {
      for (const btn of tabButtons) {
        btn.classList.toggle('v5-tab-active', btn.getAttribute('data-tab') === tabId);
      }
    }

    function selectSuite(name: string): void {
      for (const [n, card] of cardMap) {
        card.classList.toggle('suite-card-active', n === name);
      }
      selectedSuite = name;
      currentSearch = '';
      activeTab = 'runs';
      activateTab('runs');
      tabBar.style.display = '';
      // Pre-fetch schema for commit display resolution (cached after first call)
      getTestSuiteInfoCached(name).then(info => {
        commitFields = info.schema.commit_fields;
      }).catch(() => { /* graceful degradation: commitFields stays [] */ });
      syncUrl();
      loadTabContent();
    }

    function syncUrl(): void {
      const params = new URLSearchParams();
      if (selectedSuite) params.set('suite', selectedSuite);
      if (activeTab && activeTab !== 'runs') params.set('tab', activeTab);
      if (currentSearch) params.set('search', currentSearch);
      const qs = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
    }

    function loadTabContent(): void {
      // Clean up any resources from the previous tab
      tabCleanupFns.forEach(fn => fn());
      tabCleanupFns = [];
      // Abort any previous tab load to prevent race conditions
      if (tabController) tabController.abort();
      tabController = new AbortController();
      const signal = tabController.signal;

      tabContent.replaceChildren();
      if (!selectedSuite) return;

      switch (activeTab) {
        case 'runs': {
          const runsDisplayMap = new Map<string, string>();
          // The Runs tab does not show a search input because the GET /runs
          // API only supports param.X=Y filtering, not free-text search.
          renderCursorPaginatedTab(tabContent, selectedSuite, '', signal,
            '', 'Loading runs...', 'No runs found.',
            'Failed to load runs',
            (s, opts, sig) => getRunsPage(s, {
              sort: '-submitted_at',
              limit: opts.limit,
              cursor: opts.cursor,
            }, sig),
            runsColumns(selectedSuite, runsDisplayMap),
            undefined,
            async (items: RunInfo[], sig: AbortSignal) => {
              const commits = [...new Set(items.map(r => r.commit))];
              const resolved = await resolveDisplayMap(selectedSuite, commits, sig);
              for (const [k, v] of resolved) runsDisplayMap.set(k, v);
            });
          break;
        }
        case 'commits':
          renderCursorPaginatedTab(tabContent, selectedSuite, currentSearch, signal,
            'Search commits...', 'Loading commits...', 'No commits found.',
            'Failed to load commits',
            (s, opts, sig) => getCommitsPage(s, {
              search: opts.search || undefined,
              limit: opts.limit,
              cursor: opts.cursor,
            }, sig),
            commitsColumns(selectedSuite, commitFields),
            (search: string) => { currentSearch = search; syncUrl(); });
          break;
        case 'regressions':
          renderRegressionTab({
            container: tabContent,
            testsuite: selectedSuite,
            signal,
            trackCleanup: (fn) => tabCleanupFns.push(fn),
            detailLink: (text, path) => detailLink(text, selectedSuite, path),
            navigateToDetail: (uuid) => {
              window.location.href = suiteHref(selectedSuite,
                `/regressions/${encodeURIComponent(uuid)}`);
            },
          });
          break;
      }
    }

    // Load initial content if suite was pre-selected from URL
    if (selectedSuite) {
      getTestSuiteInfoCached(selectedSuite).then(info => {
        commitFields = info.schema.commit_fields;
      }).catch(() => {});
      loadTabContent();
    }
  },

  unmount(): void {
    tabCleanupFns.forEach(fn => fn());
    tabCleanupFns = [];
    if (tabController) { tabController.abort(); tabController = null; }
  },
};

// ---------------------------------------------------------------------------
// Cursor-paginated tab (shared by Runs and Commits)
// ---------------------------------------------------------------------------

interface CursorFetchOpts {
  search: string | undefined;
  limit: number;
  cursor: string | undefined;
}

/**
 * Generic cursor-paginated tab with search input, data table, and Previous/Next.
 * Used by the Runs and Commits tabs.
 */
function renderCursorPaginatedTab<T>(
  container: HTMLElement,
  suite: string,
  initialSearch: string,
  signal: AbortSignal,
  placeholder: string,
  loadingMsg: string,
  emptyMsg: string,
  errorPrefix: string,
  fetchPage: (suite: string, opts: CursorFetchOpts, signal: AbortSignal) => Promise<CursorPageResult<T>>,
  columns: Column<T>[],
  onSearchChange?: (search: string) => void,
  onPageLoaded?: (items: T[], signal: AbortSignal) => Promise<void>,
): void {
  let currentSearch = initialSearch;
  const cursorStack: string[] = [];
  let currentCursor: string | undefined;

  if (placeholder && onSearchChange) {
    const searchRow = el('div', { class: 'table-controls' });
    const searchInput = el('input', {
      type: 'text',
      class: 'test-filter-input',
      placeholder,
    }) as HTMLInputElement;
    searchInput.value = initialSearch;
    searchRow.append(searchInput);
    container.append(searchRow);

    const onInput = debounce(() => {
      currentSearch = searchInput.value.trim();
      cursorStack.length = 0;
      currentCursor = undefined;
      onSearchChange(currentSearch);
      loadPage();
    }, 300);

    searchInput.addEventListener('input', onInput as EventListener);
  }

  const tableContainer = el('div', {});
  const paginationContainer = el('div', {});
  container.append(tableContainer, paginationContainer);

  async function loadPage(): Promise<void> {
    tableContainer.replaceChildren();
    paginationContainer.replaceChildren();
    tableContainer.append(el('p', { class: 'progress-label' }, loadingMsg));

    try {
      const result = await fetchPage(suite, {
        search: currentSearch || undefined,
        limit: PAGE_SIZE,
        cursor: currentCursor,
      }, signal);

      if (onPageLoaded) await onPageLoaded(result.items, signal);

      tableContainer.replaceChildren();

      renderDataTable(tableContainer, {
        columns,
        rows: result.items,
        emptyMessage: emptyMsg,
      });

      if (cursorStack.length > 0 || result.nextCursor) {
        renderPagination(paginationContainer, {
          hasPrevious: cursorStack.length > 0,
          hasNext: !!result.nextCursor,
          onPrevious: () => {
            currentCursor = cursorStack.pop();
            loadPage();
          },
          onNext: () => {
            if (currentCursor !== undefined) cursorStack.push(currentCursor);
            currentCursor = result.nextCursor!;
            loadPage();
          },
        });
      }
    } catch (e: unknown) {
      tableContainer.replaceChildren();
      tableContainer.append(el('p', { class: 'error-banner' }, `${errorPrefix}: ${e}`));
    }
  }

  loadPage();
}

function runsColumns(suite: string, displayMap: Map<string, string>): Column<RunInfo>[] {
  return [
    { key: 'uuid', label: 'Run',
      render: (r: RunInfo) =>
        detailLink(truncate(r.uuid, 8), suite, `/runs/${encodeURIComponent(r.uuid)}`) },
    { key: 'commit', label: 'Commit',
      render: (r: RunInfo) =>
        detailLink(truncate(displayMap.get(r.commit) ?? r.commit, 12), suite,
          `/commits/${encodeURIComponent(r.commit)}`) },
    { key: 'parameters', label: 'Parameters',
      render: (r: RunInfo) => formatParamQuery(r.run_parameters || {}),
      sortable: false },
    { key: 'submitted_at', label: 'Submitted',
      render: (r: RunInfo) => formatTime(r.submitted_at) },
  ];
}

function commitsColumns(
  suite: string,
  commitFields: Array<{ name: string; display?: boolean }>,
): Column<CommitSummary>[] {
  return [
    { key: 'commit', label: 'Commit',
      render: (o: CommitSummary) =>
        detailLink(
          commitDisplayValue(o, commitFields),
          suite, `/commits/${encodeURIComponent(o.commit)}`) },
    { key: 'ordinal', label: 'Ordinal',
      render: (o: CommitSummary) => o.ordinal != null ? String(o.ordinal) : '\u2014' },
    { key: 'tag', label: 'Tag',
      render: (o: CommitSummary) => o.tag ?? '\u2014' },
  ];
}

