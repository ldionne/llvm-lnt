// pages/graph/controls.ts — Control panel for the Graph page.
// Suite dropdown, trace search chips, metric selector, test filter,
// aggregation dropdowns, regression toggle.

import { el, debounce, updateFilterValidation } from '../../utils';
import type { FieldInfo, AggFn } from '../../types';
import { formatParamQuery } from '../../types';
import { createSearchChips, chipsToParams, type SearchChipsHandle } from '../../components/search-chips';
import { filterMetricFields, renderMetricSelector, renderEmptyMetricSelector } from '../../components/metric-selector';
import type { GraphState, RegressionAnnotationMode } from './state';
import { assignSymbolChar } from './traces';

// ---- Types ----

export interface ControlsHandle {
  /** Replace the metric selector with new fields. */
  updateMetricSelector(fields: FieldInfo[], currentMetric: string): void;
  /** Re-render trace chips (after add/remove). */
  updateTraceChips(traces: Array<Record<string, string>>): void;
  /** Enable or disable all controls (disabled when no suite). */
  setEnabled(enabled: boolean): void;
  /** Update the search chips for a new suite. */
  setSuite(suite: string): void;
  /** Programmatically set the regression mode dropdown (does NOT fire callback). */
  setRegressionMode(mode: RegressionAnnotationMode): void;
  /** Embed an element (e.g. baseline panel) at the end of the first controls row. */
  embedInRow1(element: HTMLElement): void;
  /** The controls panel DOM element. */
  getElement(): HTMLElement;
  /** Destroy all sub-component handles. */
  destroy(): void;
}

export interface ControlsCallbacks {
  onSuiteChange(suite: string): void;
  onTraceAdd(params: Record<string, string>): void;
  onTraceRemove(params: Record<string, string>): void;
  onMetricChange(metric: string): void;
  onFilterChange(filter: string): void;
  onRunAggChange(agg: AggFn): void;
  onSampleAggChange(agg: AggFn): void;
  onRegressionModeChange(mode: RegressionAnnotationMode): void;
}

// ---- Helpers ----

function createAggSelect(label: string, current: AggFn, onChange: (agg: AggFn) => void): HTMLElement {
  const group = el('div', { class: 'control-group' });
  group.append(el('label', {}, label));
  const select = el('select', {}) as HTMLSelectElement;
  for (const opt of ['median', 'mean', 'min', 'max'] as AggFn[]) {
    const option = el('option', { value: opt }, opt);
    if (opt === current) (option as HTMLOptionElement).selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => onChange(select.value as AggFn));
  group.append(select);
  return group;
}

function createRegressionToggle(current: RegressionAnnotationMode, onChange: (mode: RegressionAnnotationMode) => void): { element: HTMLElement; select: HTMLSelectElement } {
  const group = el('div', { class: 'control-group' });
  group.append(el('label', {}, 'Regressions'));
  const select = el('select', { class: 'metric-select' }) as HTMLSelectElement;
  for (const [value, label] of [['off', 'Off'], ['active', 'Active'], ['all', 'All']] as const) {
    const option = el('option', { value }, label);
    if (value === current) (option as HTMLOptionElement).selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => onChange(select.value as RegressionAnnotationMode));
  group.append(select);
  return { element: group, select };
}

// ---- Main export ----

export function createControls(
  state: GraphState,
  suites: string[],
  callbacks: ControlsCallbacks,
): ControlsHandle {
  const panel = el('div', { class: 'controls-panel' });

  // Row 1: Suite + Trace search chips + Trace chips
  const row1 = el('div', { class: 'controls-row controls-row-top' });

  // Suite selector
  const suiteGroup = el('div', { class: 'control-group' });
  suiteGroup.append(el('label', {}, 'Suite'));
  const suiteSelect = el('select', { class: 'suite-select' }) as HTMLSelectElement;
  suiteSelect.append(el('option', { value: '' }, '-- Select suite --'));
  for (const s of suites) {
    const opt = el('option', { value: s }, s);
    if (s === state.suite) (opt as HTMLOptionElement).selected = true;
    suiteSelect.append(opt);
  }
  suiteSelect.addEventListener('change', () => callbacks.onSuiteChange(suiteSelect.value));
  suiteGroup.append(suiteSelect);
  row1.append(suiteGroup);

  // Trace search chips and chips display
  const traceGroup = el('div', { class: 'control-group' });
  traceGroup.append(el('label', {}, 'Traces'));
  const traceComboContainer = el('div', {});
  const chipsContainer = el('div', { class: 'trace-chips' });
  traceGroup.append(traceComboContainer, chipsContainer);
  row1.append(traceGroup);

  panel.append(row1);

  // Row 2: Metric + Aggregation + Filter + Regressions
  const row2 = el('div', { class: 'controls-row' });

  // Metric selector placeholder
  const metricContainer = el('div', { class: 'metric-container' });
  renderEmptyMetricSelector(metricContainer);
  row2.append(metricContainer);

  // Aggregation selectors
  row2.append(createAggSelect('Run aggregation', state.runAgg, callbacks.onRunAggChange));
  row2.append(createAggSelect('Sample aggregation', state.sampleAgg, callbacks.onSampleAggChange));

  // Test filter
  const filterGroup = el('div', { class: 'control-group' });
  filterGroup.append(el('label', {}, 'Filter tests'));
  const filterInput = el('input', {
    type: 'text',
    class: 'test-filter-input',
    placeholder: 'Filter tests...',
    value: state.testFilter,
  }) as HTMLInputElement;
  const debouncedFilter = debounce(() => callbacks.onFilterChange(filterInput.value), 200);
  filterInput.addEventListener('input', () => {
    updateFilterValidation(filterInput);
    debouncedFilter();
  });
  filterGroup.append(filterInput);
  row2.append(filterGroup);

  // Regression toggle
  const regressionToggle = createRegressionToggle(state.regressionMode, callbacks.onRegressionModeChange);
  row2.append(regressionToggle.element);

  panel.append(row2);

  // --- Search chips handle for trace input ---
  let searchChipsHandle: SearchChipsHandle | null = null;

  function createTraceSearchChips(suite: string): void {
    if (searchChipsHandle) {
      searchChipsHandle.destroy();
      searchChipsHandle = null;
    }
    traceComboContainer.replaceChildren();
    if (!suite) return;
    searchChipsHandle = createSearchChips({
      testsuite: suite,
      placeholder: 'Add trace (param filter)...',
      onChange: (chips) => {
        if (chips.length > 0) {
          const params = chipsToParams(chips);
          callbacks.onTraceAdd(params);
          // Clear the chips input after adding a trace
          searchChipsHandle?.setChips([]);
        }
      },
    });
    traceComboContainer.append(searchChipsHandle.element);
  }

  createTraceSearchChips(state.suite);

  // --- Trace chips rendering ---

  function renderChips(traces: Array<Record<string, string>>): void {
    chipsContainer.replaceChildren();
    for (let i = 0; i < traces.length; i++) {
      const params = traces[i];
      const label = formatParamQuery(params) || '(all)';
      const chip = el('span', { class: 'trace-chip' });
      const symbolSpan = el('span', { class: 'chip-symbol' }, assignSymbolChar(i));
      const nameSpan = el('span', {}, label);
      const removeBtn = el('button', {
        type: 'button',
        class: 'chip-remove',
        'aria-label': `Remove ${label}`,
      }, '\u00d7');
      removeBtn.addEventListener('click', () => callbacks.onTraceRemove(params));
      chip.append(symbolSpan, nameSpan, removeBtn);
      chipsContainer.append(chip);
    }
  }

  // Render initial trace chips (empty; populated by index.ts after mount)
  // For backward compatibility, legacy ?machine= params are converted to traces by index.ts
  renderChips([]);

  // --- Enable/disable ---

  function setEnabled(enabled: boolean): void {
    const inputs = panel.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select');
    for (const inp of inputs) {
      if (inp === suiteSelect) continue; // suite selector always enabled
      inp.disabled = !enabled;
    }
  }

  if (!state.suite) setEnabled(false);

  return {
    updateMetricSelector(fields: FieldInfo[], currentMetric: string): void {
      metricContainer.replaceChildren();
      const metricFields = filterMetricFields(fields);
      if (metricFields.length > 0) {
        renderMetricSelector(metricContainer, metricFields, callbacks.onMetricChange, currentMetric, { placeholder: true });
      } else {
        renderEmptyMetricSelector(metricContainer);
      }
    },

    updateTraceChips(traces: Array<Record<string, string>>): void {
      renderChips(traces);
    },

    setEnabled,

    setSuite(suite: string): void {
      createTraceSearchChips(suite);
      setEnabled(!!suite);
    },

    setRegressionMode(mode: RegressionAnnotationMode): void {
      regressionToggle.select.value = mode;
    },

    embedInRow1(element: HTMLElement): void {
      row1.append(element);
    },

    getElement(): HTMLElement {
      return panel;
    },

    destroy(): void {
      if (searchChipsHandle) searchChipsHandle.destroy();
    },
  };
}
