// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock API module
vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  return {
    ...actual,
    getTestSuiteInfoCached: vi.fn().mockResolvedValue({
      name: 'nts',
      schema: {
        metrics: [
          { name: 'exec_time', type: 'real', display_name: 'Exec Time', unit: 's', unit_abbrev: 's', bigger_is_better: false },
        ],
        commit_fields: [],
      },
    }),
    fetchOneCursorPage: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
    postOneCursorPage: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
    apiUrl: vi.fn((suite: string, path: string) => `/api/v5/${suite}/${path}`),
  };
});

vi.mock('../../../router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../router')>();
  return { ...actual, navigate: vi.fn(), getTestsuites: vi.fn(() => ['nts']) };
});

vi.mock('../../../utils', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../utils')>();
  return { ...actual, resolveDisplayMap: vi.fn().mockResolvedValue(new Map()) };
});

const mockSearchChipsHandle = { destroy: vi.fn(), setChips: vi.fn(), element: document.createElement('div') };
vi.mock('../../../components/search-chips', () => ({
  createSearchChips: vi.fn(() => mockSearchChipsHandle),
  chipsToParams: vi.fn(() => ({})),
}));

const mockCommitPickerHandle = {
  element: document.createElement('div'),
  input: document.createElement('input'),
  destroy: vi.fn(),
};
vi.mock('../../../combobox', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../combobox')>();
  return {
    ...actual,
    createCommitPicker: vi.fn(() => mockCommitPickerHandle),
  };
});

(globalThis as unknown as Record<string, unknown>).Plotly = {
  newPlot: vi.fn().mockResolvedValue(document.createElement('div')),
  react: vi.fn(),
  purge: vi.fn(),
  restyle: vi.fn(),
  addTraces: vi.fn(),
  deleteTraces: vi.fn(),
  relayout: vi.fn(),
  Fx: { hover: vi.fn(), unhover: vi.fn() },
};

import { graphPage } from '../../../pages/graph/index';
import { createSearchChips } from '../../../components/search-chips';
import { resolveDisplayMap } from '../../../utils';
import { GRAPH_CHART_DBLCLICK, GRAPH_TABLE_HOVER } from '../../../events';

describe('graphPage', () => {
  let container: HTMLElement;

  const params = { testsuite: '' };

  beforeEach(() => {
    vi.clearAllMocks();
    mockSearchChipsHandle.element = document.createElement('div');
    container = document.createElement('div');
    // Default empty URL state
    Object.defineProperty(window, 'location', {
      value: { ...window.location, search: '', pathname: '/v5/graph' },
      writable: true,
    });
  });

  afterEach(() => {
    graphPage.unmount?.();
    vi.unstubAllGlobals();
  });

  it('renders page header and controls panel', () => {
    graphPage.mount(container, params);
    expect(container.querySelector('.page-header')?.textContent).toBe('Graph');
    expect(container.querySelector('.controls-panel')).not.toBeNull();
  });

  it('renders suite selector with options', () => {
    graphPage.mount(container, params);
    const suiteSelect = container.querySelector('.suite-select') as HTMLSelectElement;
    expect(suiteSelect).not.toBeNull();
    expect(suiteSelect.options.length).toBeGreaterThan(1);
    expect(suiteSelect.options[1].value).toBe('nts');
  });

  it('renders baseline panel', () => {
    graphPage.mount(container, params);
    expect(container.querySelector('.baseline-panel')).not.toBeNull();
  });

  it('renders controls panel with search chips', () => {
    // Mount with suite pre-selected
    Object.defineProperty(window, 'location', {
      value: { ...window.location, search: '?suite=nts', pathname: '/v5/graph' },
      writable: true,
    });
    graphPage.mount(container, params);
    // Search chips should be created for the suite
    expect(createSearchChips).toHaveBeenCalledWith(
      expect.objectContaining({ testsuite: 'nts' }),
    );
  });

  it('unmount cleans up without errors', () => {
    graphPage.mount(container, params);
    expect(() => graphPage.unmount?.()).not.toThrow();
  });

  it('can mount again after unmount', () => {
    graphPage.mount(container, params);
    graphPage.unmount?.();
    const container2 = document.createElement('div');
    expect(() => graphPage.mount(container2, params)).not.toThrow();
    graphPage.unmount?.();
  });

  it('suite change resets regression mode dropdown to off', async () => {
    // Mount with regressions=active in URL
    Object.defineProperty(window, 'location', {
      value: { ...window.location, search: '?suite=nts&regressions=active', pathname: '/v5/graph' },
      writable: true,
    });
    graphPage.mount(container, params);

    // Verify initial regression dropdown shows 'active'
    const selects = container.querySelectorAll<HTMLSelectElement>('select');
    const regressionSelect = [...selects].find(s =>
      [...s.options].some(o => o.value === 'active' && o.text === 'Active'),
    );
    expect(regressionSelect).toBeDefined();
    expect(regressionSelect!.value).toBe('active');

    // Trigger suite change
    const suiteSelect = container.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = '';
    suiteSelect.dispatchEvent(new Event('change'));

    // Regression dropdown should reset to 'off'
    expect(regressionSelect!.value).toBe('off');
  });

  it('does not resolve baseline display values when no baselines in URL', () => {
    graphPage.mount(container, params);
    expect(resolveDisplayMap).not.toHaveBeenCalled();
  });

  // ===========================================================================
  // Error handling
  // ===========================================================================

  it('showError displays banner then auto-hides after 5s', async () => {
    vi.useFakeTimers();
    try {
      const { getTestSuiteInfoCached } = await import('../../../api');
      vi.mocked(getTestSuiteInfoCached).mockRejectedValueOnce(new Error('fail'));

      Object.defineProperty(window, 'location', {
        value: { ...window.location, search: '?suite=nts', pathname: '/v5/graph' },
        writable: true,
      });
      graphPage.mount(container, params);

      await vi.waitFor(() => {
        const banner = container.querySelector('.error-banner') as HTMLElement;
        expect(banner).not.toBeNull();
        expect(banner.style.display).not.toBe('none');
        expect(banner.textContent).toContain('Failed to load suite fields');
      });

      vi.advanceTimersByTime(5000);

      const banner = container.querySelector('.error-banner') as HTMLElement;
      expect(banner.style.display).toBe('none');
    } finally {
      vi.useRealTimers();
    }
  });
});
