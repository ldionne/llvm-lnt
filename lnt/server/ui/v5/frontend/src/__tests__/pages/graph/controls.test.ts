// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockSearchChipsHandle = { destroy: vi.fn(), setChips: vi.fn(), element: document.createElement('div') };
vi.mock('../../../components/search-chips', () => ({
  createSearchChips: vi.fn(() => mockSearchChipsHandle),
  chipsToParams: vi.fn(() => ({})),
}));

vi.mock('../../../components/metric-selector', () => ({
  filterMetricFields: vi.fn((fields: unknown[]) => fields),
  renderMetricSelector: vi.fn((container: HTMLElement, fields: unknown[], onChange: (m: string) => void, initial?: string) => {
    const group = document.createElement('div');
    group.className = 'control-group';
    const select = document.createElement('select');
    select.className = 'metric-select';
    for (const f of fields as Array<{ name: string }>) {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name;
      select.append(opt);
    }
    if (initial) select.value = initial;
    select.addEventListener('change', () => onChange(select.value));
    group.append(select);
    container.append(group);
    return select.value;
  }),
  renderEmptyMetricSelector: vi.fn((container: HTMLElement) => {
    const div = document.createElement('div');
    div.className = 'metric-placeholder';
    container.append(div);
  }),
}));

import { createControls, type ControlsCallbacks } from '../../../pages/graph/controls';
import { createSearchChips } from '../../../components/search-chips';
import { filterMetricFields, renderMetricSelector, renderEmptyMetricSelector } from '../../../components/metric-selector';
import type { GraphState } from '../../../pages/graph/state';

function makeState(overrides?: Partial<GraphState>): GraphState {
  return {
    suite: 'nts',
    traces: [{ compiler: 'clang' }],
    machines: [],
    metric: 'exec_time',
    testFilter: '',
    runAgg: 'median',
    sampleAgg: 'median',
    baselines: [],
    regressionMode: 'off',
    ...overrides,
  };
}

function makeCallbacks(): { [K in keyof ControlsCallbacks]: ReturnType<typeof vi.fn> } {
  return {
    onSuiteChange: vi.fn(),
    onTraceAdd: vi.fn(),
    onTraceRemove: vi.fn(),
    onMetricChange: vi.fn(),
    onFilterChange: vi.fn(),
    onRunAggChange: vi.fn(),
    onSampleAggChange: vi.fn(),
    onRegressionModeChange: vi.fn(),
  };
}

function findSelectByOptions(panel: HTMLElement, ...optionValues: string[]): HTMLSelectElement | undefined {
  const selects = panel.querySelectorAll<HTMLSelectElement>('select');
  return [...selects].find(s =>
    optionValues.every(v => [...s.options].some(o => o.value === v)),
  );
}

describe('createControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearchChipsHandle.element = document.createElement('div');
  });

  // ---- DOM structure ----

  it('renders suite dropdown with placeholder and all suites, pre-selecting current', () => {
    const handle = createControls(makeState({ suite: 'nts' }), ['nts', 'other'], makeCallbacks());
    const panel = handle.getElement();

    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    expect(suiteSelect).not.toBeNull();
    expect(suiteSelect.options[0].value).toBe('');
    expect(suiteSelect.options[0].textContent).toBe('-- Select suite --');
    expect([...suiteSelect.options].map(o => o.value)).toContain('nts');
    expect([...suiteSelect.options].map(o => o.value)).toContain('other');
    expect(suiteSelect.value).toBe('nts');
  });

  it('creates search chips for current suite', () => {
    createControls(makeState({ suite: 'nts' }), ['nts'], makeCallbacks());
    expect(createSearchChips).toHaveBeenCalledWith(
      expect.objectContaining({ testsuite: 'nts' }),
    );
  });

  it('renders agg dropdowns, filter input, and regression toggle with initial values', () => {
    const handle = createControls(
      makeState({ runAgg: 'mean', sampleAgg: 'max', testFilter: 'foo', regressionMode: 'active' }),
      ['nts'],
      makeCallbacks(),
    );
    const panel = handle.getElement();

    const runAggSelect = findSelectByOptions(panel, 'median', 'mean', 'min', 'max');
    expect(runAggSelect).toBeDefined();
    // There are two agg selects — find the one with value 'mean' vs 'max'
    const aggSelects = [...panel.querySelectorAll<HTMLSelectElement>('select')]
      .filter(s => [...s.options].some(o => o.value === 'median') && [...s.options].some(o => o.value === 'max') && s.options.length === 4);
    expect(aggSelects.length).toBe(2);
    expect(aggSelects[0].value).toBe('mean');
    expect(aggSelects[1].value).toBe('max');

    const filterInput = panel.querySelector('.test-filter-input') as HTMLInputElement;
    expect(filterInput).not.toBeNull();
    expect(filterInput.value).toBe('foo');

    const regSelect = findSelectByOptions(panel, 'off', 'active', 'all');
    expect(regSelect).toBeDefined();
    expect(regSelect!.value).toBe('active');
  });

  // ---- Callbacks ----

  it('suite change fires onSuiteChange', () => {
    const callbacks = makeCallbacks();
    const handle = createControls(makeState(), ['nts', 'other'], callbacks);
    const panel = handle.getElement();

    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'other';
    suiteSelect.dispatchEvent(new Event('change'));
    expect(callbacks.onSuiteChange).toHaveBeenCalledWith('other');
  });

  it('agg changes fire onRunAggChange / onSampleAggChange', () => {
    const callbacks = makeCallbacks();
    const handle = createControls(makeState(), ['nts'], callbacks);
    const panel = handle.getElement();

    const aggSelects = [...panel.querySelectorAll<HTMLSelectElement>('select')]
      .filter(s => [...s.options].some(o => o.value === 'median') && s.options.length === 4);

    aggSelects[0].value = 'mean';
    aggSelects[0].dispatchEvent(new Event('change'));
    expect(callbacks.onRunAggChange).toHaveBeenCalledWith('mean');

    aggSelects[1].value = 'max';
    aggSelects[1].dispatchEvent(new Event('change'));
    expect(callbacks.onSampleAggChange).toHaveBeenCalledWith('max');
  });

  it('regression mode change fires onRegressionModeChange', () => {
    const callbacks = makeCallbacks();
    const handle = createControls(makeState(), ['nts'], callbacks);
    const panel = handle.getElement();

    const regSelect = findSelectByOptions(panel, 'off', 'active', 'all')!;
    regSelect.value = 'all';
    regSelect.dispatchEvent(new Event('change'));
    expect(callbacks.onRegressionModeChange).toHaveBeenCalledWith('all');
  });

  it('filter input fires onFilterChange after 200ms debounce, not before', () => {
    vi.useFakeTimers();
    try {
      const callbacks = makeCallbacks();
      const handle = createControls(makeState(), ['nts'], callbacks);
      const panel = handle.getElement();

      const filterInput = panel.querySelector('.test-filter-input') as HTMLInputElement;
      filterInput.value = 'abc';
      filterInput.dispatchEvent(new Event('input'));

      expect(callbacks.onFilterChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(199);
      expect(callbacks.onFilterChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(callbacks.onFilterChange).toHaveBeenCalledWith('abc');

      // Rapid typing resets the timer
      callbacks.onFilterChange.mockClear();
      filterInput.value = 'x';
      filterInput.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(100);
      filterInput.value = 'xy';
      filterInput.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(200);
      expect(callbacks.onFilterChange).toHaveBeenCalledTimes(1);
      expect(callbacks.onFilterChange).toHaveBeenCalledWith('xy');
    } finally {
      vi.useRealTimers();
    }
  });

  // ---- Handle methods ----

  it('setEnabled(false) disables all inputs except suite selector', () => {
    const handle = createControls(makeState(), ['nts'], makeCallbacks());
    const panel = handle.getElement();
    handle.setEnabled(false);

    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    expect(suiteSelect.disabled).toBe(false);

    const otherInputs = [...panel.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')]
      .filter(el => el !== suiteSelect);
    expect(otherInputs.length).toBeGreaterThan(0);
    for (const inp of otherInputs) {
      expect(inp.disabled).toBe(true);
    }
  });

  it('setEnabled(true) re-enables all controls', () => {
    const handle = createControls(makeState(), ['nts'], makeCallbacks());
    const panel = handle.getElement();
    handle.setEnabled(false);
    handle.setEnabled(true);

    const allInputs = panel.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select');
    for (const inp of allInputs) {
      expect(inp.disabled).toBe(false);
    }
  });

  it('setSuite destroys old search chips, creates new ones, and updates enabled state', () => {
    const handle = createControls(makeState({ suite: 'nts' }), ['nts', 'other'], makeCallbacks());
    vi.mocked(createSearchChips).mockClear();
    mockSearchChipsHandle.destroy.mockClear();

    handle.setSuite('other');
    expect(mockSearchChipsHandle.destroy).toHaveBeenCalled();
    expect(createSearchChips).toHaveBeenCalledWith(
      expect.objectContaining({ testsuite: 'other' }),
    );

    // setSuite('') disables controls
    vi.mocked(createSearchChips).mockClear();
    handle.setSuite('');
    const panel = handle.getElement();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    const otherInputs = [...panel.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')]
      .filter(el => el !== suiteSelect);
    for (const inp of otherInputs) {
      expect(inp.disabled).toBe(true);
    }
  });

  it('setRegressionMode updates dropdown value without firing callback', () => {
    const callbacks = makeCallbacks();
    const handle = createControls(makeState(), ['nts'], callbacks);
    handle.setRegressionMode('all');

    const panel = handle.getElement();
    const regSelect = findSelectByOptions(panel, 'off', 'active', 'all')!;
    expect(regSelect.value).toBe('all');
    expect(callbacks.onRegressionModeChange).not.toHaveBeenCalled();
  });

  it('updateTraceChips replaces chips with new list', () => {
    const handle = createControls(makeState({ traces: [{ compiler: 'clang' }] }), ['nts'], makeCallbacks());
    const panel = handle.getElement();

    handle.updateTraceChips([{ compiler: 'clang' }, { os: 'linux' }, { arch: 'x86' }]);
    const chips = panel.querySelectorAll('.trace-chip');
    expect(chips.length).toBe(3);
    expect(chips[0].querySelector('.chip-symbol')!.textContent).toBe('\u25CF');
    expect(chips[1].querySelector('.chip-symbol')!.textContent).toBe('\u25B2');
    expect(chips[2].querySelector('.chip-symbol')!.textContent).toBe('\u25A0');
  });

  it('updateMetricSelector replaces container with real selector or empty placeholder', () => {
    const handle = createControls(makeState(), ['nts'], makeCallbacks());

    const fields = [{ name: 'exec_time', type: 'real', display_name: 'Exec Time', unit: 's', unit_abbrev: 's', bigger_is_better: false }];
    handle.updateMetricSelector(fields, 'exec_time');
    expect(filterMetricFields).toHaveBeenCalledWith(fields);
    expect(renderMetricSelector).toHaveBeenCalled();

    vi.mocked(renderEmptyMetricSelector).mockClear();
    handle.updateMetricSelector([], '');
    expect(renderEmptyMetricSelector).toHaveBeenCalled();
  });

  // ---- Lifecycle ----

  it('destroy calls searchChipsHandle.destroy', () => {
    const handle = createControls(makeState(), ['nts'], makeCallbacks());
    mockSearchChipsHandle.destroy.mockClear();
    handle.destroy();
    expect(mockSearchChipsHandle.destroy).toHaveBeenCalled();
  });
});
