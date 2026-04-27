// pages/home.ts — Dashboard page with sparkline trend overview.
// Suite-agnostic — served at /v5/.

import type { PageModule, RouteParams } from '../router';
import { getTestsuites } from '../router';
import { getDashboard, fetchTrends } from '../api';
import { el, agnosticUrl, traceColor } from '../utils';
import type { SparklineTrace } from '../components/sparkline-card';
import {
  createSparklineCard, createSparklineLoading, createSparklineError,
} from '../components/sparkline-card';
import { formatParamQuery, encodeParamQuery } from '../types';

const MAX_CARDS_PER_SUITE = 10;

type RangePreset = '100' | '500' | '1000';
const RANGE_COMMITS: Record<RangePreset, number> = { '100': 100, '500': 500, '1000': 1000 };
const RANGE_PRESETS: RangePreset[] = ['100', '500', '1000'];

function isValidRange(s: string): s is RangePreset {
  return RANGE_PRESETS.includes(s as RangePreset);
}

// ---------------------------------------------------------------------------
// Data fetching — uses server-side trends endpoint for geomean aggregation
// ---------------------------------------------------------------------------

/**
 * Fetch trend data for one dashboard card (metric + params).
 * Returns a single sparkline trace with server-computed geomean values per commit.
 * Points are assigned sequential x-indices for even spacing on the chart.
 */
async function fetchCardTrends(
  suite: string,
  metric: string,
  params: Record<string, string>,
  lastN: number,
  signal: AbortSignal,
): Promise<SparklineTrace[]> {
  const items = await fetchTrends(suite, { metric, params, lastN }, signal);

  // Sort by ordinal and assign sequential x-indices
  const sorted = [...items].sort((a, b) => a.ordinal - b.ordinal);
  const points = sorted.map((item, idx) => ({
    x: idx,
    value: item.value,
    commit: item.commit,
  }));

  if (points.length === 0) return [];

  const label = formatParamQuery(params) || '(all)';
  return [{
    label,
    color: traceColor(0),
    points,
  }];
}

// ---------------------------------------------------------------------------
// Dashboard page module
// ---------------------------------------------------------------------------

/** Track all Plotly card destroy callbacks for cleanup on unmount. */
let destroyFns: Array<() => void> = [];
let abortController: AbortController | null = null;

export const homePage: PageModule = {
  mount(container: HTMLElement, _params: RouteParams): void {
    // Clean up any previous state
    cleanup();

    abortController = new AbortController();
    const signal = abortController.signal;

    const suites = getTestsuites();

    // Read range from URL
    const urlParams = new URLSearchParams(window.location.search);
    let activeRange: RangePreset = '500';
    const rangeParam = urlParams.get('range') || '';
    if (isValidRange(rangeParam)) activeRange = rangeParam;

    // Header with commit range buttons
    const rangeGroup = el('div', { class: 'dashboard-range-group' });
    const rangeButtons = new Map<RangePreset, HTMLButtonElement>();
    for (const preset of RANGE_PRESETS) {
      const btn = el('button', {
        class: `dashboard-range-btn${preset === activeRange ? ' dashboard-range-btn-active' : ''}`,
      }, `Last ${preset}`);
      btn.addEventListener('click', () => {
        if (preset === activeRange) return;
        activeRange = preset;
        syncUrl();
        for (const [p, b] of rangeButtons) {
          b.className = `dashboard-range-btn${p === activeRange ? ' dashboard-range-btn-active' : ''}`;
        }
        reloadAll();
      });
      rangeButtons.set(preset, btn);
      rangeGroup.append(btn);
    }

    const header = el('div', { class: 'dashboard-header' },
      el('h2', { class: 'page-header' }, 'Dashboard'),
      rangeGroup,
    );
    container.append(header);

    if (suites.length === 0) {
      container.append(el('p', {}, 'No test suites available.'));
      return;
    }

    // Suite sections
    const suiteSections = new Map<string, HTMLElement>();
    for (const suite of suites) {
      const grid = el('div', { class: 'sparkline-grid' });
      const section = el('div', { class: 'suite-section' },
        el('h3', {}, suite),
        grid,
      );
      suiteSections.set(suite, grid);
      container.append(section);
    }

    function syncUrl(): void {
      const params = new URLSearchParams();
      if (activeRange !== '500') params.set('range', activeRange);
      const qs = params.toString();
      window.history.replaceState(null, '',
        window.location.pathname + (qs ? '?' + qs : ''));
    }

    function reloadAll(): void {
      // Abort previous requests
      if (abortController) abortController.abort();
      abortController = new AbortController();
      const sig = abortController.signal;

      // Destroy existing sparkline cards
      for (const fn of destroyFns) fn();
      destroyFns = [];

      // Clear grids
      for (const grid of suiteSections.values()) {
        grid.replaceChildren();
      }

      loadAllSuites(sig);
    }

    function loadAllSuites(sig: AbortSignal): void {
      for (const suite of suites) {
        const grid = suiteSections.get(suite)!;
        loadSuite(suite, grid, sig);
      }
    }

    async function loadSuite(suite: string, grid: HTMLElement, sig: AbortSignal): Promise<void> {
      try {
        // Fetch dashboard cards for this suite
        const cards = await getDashboard(suite, sig);

        if (sig.aborted) return;

        if (cards.length === 0) {
          grid.append(el('p', { class: 'sparkline-loading' }, 'No dashboard cards configured.'));
          return;
        }

        const limitedCards = cards.slice(0, MAX_CARDS_PER_SUITE);

        // Create loading placeholders for each card
        const placeholders = new Map<number, HTMLElement>();
        for (let i = 0; i < limitedCards.length; i++) {
          const card = limitedCards[i];
          const title = `${card.metric} | ${formatParamQuery(card.params) || '(all)'}`;
          const placeholder = createSparklineLoading(title);
          placeholders.set(i, placeholder);
          grid.append(placeholder);
        }

        // Fetch and render each card's sparkline
        const lastN = RANGE_COMMITS[activeRange];
        for (let i = 0; i < limitedCards.length; i++) {
          const card = limitedCards[i];
          loadCardSparkline(suite, card.metric, card.params, lastN, grid, placeholders, i, sig);
        }
      } catch (err) {
        if (sig.aborted) return;
        grid.append(el('p', { class: 'sparkline-error' }, `Error loading suite: ${err}`));
      }
    }

    async function loadCardSparkline(
      suite: string,
      metric: string,
      params: Record<string, string>,
      lastN: number,
      grid: HTMLElement,
      placeholders: Map<number, HTMLElement>,
      cardIndex: number,
      sig: AbortSignal,
    ): Promise<void> {
      const title = `${metric} | ${formatParamQuery(params) || '(all)'}`;

      try {
        const traces = await fetchCardTrends(suite, metric, params, lastN, sig);
        if (sig.aborted) return;

        const { element, destroy } = createSparklineCard({
          title,
          traces,
          onClick: () => {
            const urlParams = new URLSearchParams();
            urlParams.set('suite', suite);
            urlParams.set('metric', metric);
            // Encode params as a trace query
            const encoded = encodeParamQuery(params);
            if (encoded) urlParams.set('trace', encoded);
            window.location.href = agnosticUrl(`/graph?${urlParams.toString()}`);
          },
        });

        destroyFns.push(destroy);

        // Replace loading placeholder with the rendered card
        const placeholder = placeholders.get(cardIndex);
        if (placeholder && placeholder.parentElement === grid) {
          grid.replaceChild(element, placeholder);
        }
      } catch (err) {
        if (sig.aborted) return;
        const errorCard = createSparklineError(title);
        const placeholder = placeholders.get(cardIndex);
        if (placeholder && placeholder.parentElement === grid) {
          grid.replaceChild(errorCard, placeholder);
        }
      }
    }

    // Initial load
    loadAllSuites(signal);
  },

  unmount(): void {
    cleanup();
  },
};

function cleanup(): void {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  for (const fn of destroyFns) fn();
  destroyFns = [];
}
