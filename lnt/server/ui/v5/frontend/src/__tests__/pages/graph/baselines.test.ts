// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

import { createBaselinePanel, type BaselinePanelCallbacks } from '../../../pages/graph/baselines';
import { createSearchChips, chipsToParams } from '../../../components/search-chips';
import { createCommitPicker } from '../../../combobox';
import type { BaselineRef } from '../../../pages/graph/state';

function makeBaseline(suite = 'nts', params: Record<string, string> = { compiler: 'clang' }, commit = 'abc'): BaselineRef {
  return { suite, params, commit };
}

function makeCallbacks(overrides?: Partial<BaselinePanelCallbacks>): BaselinePanelCallbacks {
  return {
    onBaselineAdd: vi.fn(),
    onBaselineRemove: vi.fn(),
    getCommitFields: vi.fn(() => []),
    getBaselineCommits: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

describe('createBaselinePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCommitPickerHandle.input = document.createElement('input');
    mockCommitPickerHandle.element = document.createElement('div');
    mockSearchChipsHandle.element = document.createElement('div');
  });

  // ---- DOM structure and initial state ----

  it('renders panel with label, add button visible, and form hidden', () => {
    const handle = createBaselinePanel([], new Map(), ['nts'], makeCallbacks());
    const panel = handle.getElement();

    expect(panel.classList.contains('baseline-panel')).toBe(true);
    expect(panel.querySelector('label')?.textContent).toBe('Baselines');

    const addBtn = panel.querySelector('.baseline-add-btn') as HTMLElement;
    expect(addBtn).not.toBeNull();
    expect(addBtn.style.display).not.toBe('none');

    const form = panel.querySelector('.baseline-form') as HTMLElement;
    expect(form).not.toBeNull();
    expect(form.style.display).toBe('none');
  });

  it('renders initial baseline chips with display values from displayMap', () => {
    const bl = makeBaseline('nts', { compiler: 'clang' }, 'abc');
    const displayMap = new Map([['abc', 'v1.0']]);
    const handle = createBaselinePanel([bl], displayMap, ['nts'], makeCallbacks());
    const panel = handle.getElement();

    const chips = panel.querySelectorAll('.baseline-chip');
    expect(chips.length).toBe(1);
    expect(chips[0].textContent).toContain('nts/compiler:clang/v1.0');
  });

  it('renders suite dropdown inside form with all suites', () => {
    const handle = createBaselinePanel([], new Map(), ['nts', 'other'], makeCallbacks());
    const panel = handle.getElement();

    // Show the form
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();

    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    expect(suiteSelect).not.toBeNull();
    const values = [...suiteSelect.options].map(o => o.value);
    expect(values).toContain('');
    expect(values).toContain('nts');
    expect(values).toContain('other');
  });

  // ---- Add button toggle ----

  it('clicking add button shows form and hides the button', () => {
    const handle = createBaselinePanel([], new Map(), ['nts'], makeCallbacks());
    const panel = handle.getElement();

    const addBtn = panel.querySelector('.baseline-add-btn') as HTMLElement;
    const form = panel.querySelector('.baseline-form') as HTMLElement;
    addBtn.click();

    expect(form.style.display).toBe('');
    expect(addBtn.style.display).toBe('none');
  });

  // ---- Cascading dropdowns ----

  it('suite change creates search chips for selected suite', () => {
    const handle = createBaselinePanel([], new Map(), ['nts', 'other'], makeCallbacks());
    const panel = handle.getElement();

    // Show form and select suite
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    expect(createSearchChips).toHaveBeenCalledWith(
      expect.objectContaining({ testsuite: 'nts' }),
    );
  });

  it('suite change to empty clears search chips and commit picker', () => {
    const handle = createBaselinePanel([], new Map(), ['nts'], makeCallbacks());
    const panel = handle.getElement();

    // Show form, select suite to create search chips
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));
    expect(createSearchChips).toHaveBeenCalledTimes(1);

    // Change suite to empty
    mockSearchChipsHandle.destroy.mockClear();
    suiteSelect.value = '';
    suiteSelect.dispatchEvent(new Event('change'));
    expect(mockSearchChipsHandle.destroy).toHaveBeenCalled();
  });

  it('param selection triggers loadCommits and creates commit picker', async () => {
    const commits = [
      { commit: 'abc', ordinal: 1, tag: null, fields: {} },
      { commit: 'def', ordinal: 2, tag: null, fields: {} },
    ];
    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockResolvedValue(commits),
    });

    // Make chipsToParams return a non-empty params object
    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    // Show form, select suite
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    // Capture and trigger the search chips onChange callback
    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    await vi.waitFor(() => {
      expect(createCommitPicker).toHaveBeenCalled();
    });

    expect(callbacks.getBaselineCommits).toHaveBeenCalledWith('nts', { compiler: 'clang' }, expect.any(AbortSignal));
  });

  it('commit selection calls onBaselineAdd and resets picker input', async () => {
    const commits = [{ commit: 'abc', ordinal: 1, tag: null, fields: {} }];
    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockResolvedValue(commits),
    });

    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    // Set up cascading: show form -> select suite -> add params
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    await vi.waitFor(() => {
      expect(createCommitPicker).toHaveBeenCalled();
    });

    // Capture and trigger commit onSelect
    const pickerOpts = vi.mocked(createCommitPicker).mock.calls[0][0];
    mockCommitPickerHandle.input.value = 'abc';
    pickerOpts.onSelect('abc');

    expect(callbacks.onBaselineAdd).toHaveBeenCalledWith({
      suite: 'nts',
      params: { compiler: 'clang' },
      commit: 'abc',
    });
    expect(mockCommitPickerHandle.input.value).toBe('');
  });

  // ---- Error handling ----

  it('loadCommits shows error text when getBaselineCommits rejects', async () => {
    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockRejectedValue(new Error('Network fail')),
    });
    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    await vi.waitFor(() => {
      const errorEl = panel.querySelector('.error-text');
      expect(errorEl).not.toBeNull();
      expect(errorEl!.textContent).toBe('Failed to load commits');
    });
  });

  it('loadCommits silently ignores AbortError', async () => {
    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError')),
    });
    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    // Give the async rejection time to propagate
    await vi.waitFor(() => {
      expect(callbacks.getBaselineCommits).toHaveBeenCalled();
    });

    expect(panel.querySelector('.error-text')).toBeNull();
  });

  // ---- Chip management ----

  it('chip remove button calls onBaselineRemove', () => {
    const bl1 = makeBaseline('nts', { compiler: 'clang' }, 'abc');
    const bl2 = makeBaseline('nts', { compiler: 'gcc' }, 'def');
    const callbacks = makeCallbacks();
    const handle = createBaselinePanel([bl1, bl2], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    const removeButtons = panel.querySelectorAll('.chip-remove');
    expect(removeButtons.length).toBe(2);
    (removeButtons[0] as HTMLButtonElement).click();
    expect(callbacks.onBaselineRemove).toHaveBeenCalledWith(bl1);
  });

  it('updateChips replaces chips with new baselines and display values', () => {
    const handle = createBaselinePanel([], new Map(), ['nts'], makeCallbacks());
    const panel = handle.getElement();
    expect(panel.querySelectorAll('.baseline-chip').length).toBe(0);

    const bl = makeBaseline('nts', { compiler: 'clang' }, 'abc');
    handle.updateChips([bl], new Map([['abc', 'v2.0']]));

    const chips = panel.querySelectorAll('.baseline-chip');
    expect(chips.length).toBe(1);
    expect(chips[0].textContent).toContain('nts/compiler:clang/v2.0');
  });

  // ---- Handle methods ----

  it('reset hides form, shows add button, clears state, aborts fetch', async () => {
    const blockingPromise = new Promise<unknown[]>(() => {});

    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockReturnValue(blockingPromise),
    });
    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    // Show form, select suite, add params (starts pending fetch)
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    // Capture abort signal before reset
    const signal = (callbacks.getBaselineCommits as ReturnType<typeof vi.fn>).mock.calls[0][2] as AbortSignal;

    handle.reset();

    const form = panel.querySelector('.baseline-form') as HTMLElement;
    const addBtn = panel.querySelector('.baseline-add-btn') as HTMLElement;
    expect(form.style.display).toBe('none');
    expect(addBtn.style.display).toBe('');
    expect(suiteSelect.value).toBe('');
    expect(signal.aborted).toBe(true);
    expect(mockSearchChipsHandle.destroy).toHaveBeenCalled();
  });

  it('destroy cleans up search chips, commit picker, and abort controller', async () => {
    const commits = [{ commit: 'abc', ordinal: 1, tag: null, fields: {} }];
    const callbacks = makeCallbacks({
      getBaselineCommits: vi.fn().mockResolvedValue(commits),
    });
    vi.mocked(chipsToParams).mockReturnValue({ compiler: 'clang' });

    const handle = createBaselinePanel([], new Map(), ['nts'], callbacks);
    const panel = handle.getElement();

    // Set up all sub-components
    (panel.querySelector('.baseline-add-btn') as HTMLElement).click();
    const suiteSelect = panel.querySelector('.suite-select') as HTMLSelectElement;
    suiteSelect.value = 'nts';
    suiteSelect.dispatchEvent(new Event('change'));

    const searchChipsCall = vi.mocked(createSearchChips).mock.calls[0];
    searchChipsCall[0].onChange([{ key: 'compiler', value: 'clang' }]);

    await vi.waitFor(() => {
      expect(createCommitPicker).toHaveBeenCalled();
    });

    mockSearchChipsHandle.destroy.mockClear();
    mockCommitPickerHandle.destroy.mockClear();

    handle.destroy();
    expect(mockSearchChipsHandle.destroy).toHaveBeenCalled();
    expect(mockCommitPickerHandle.destroy).toHaveBeenCalled();
  });

  it('destroy is safe when no sub-components exist', () => {
    const handle = createBaselinePanel([], new Map(), ['nts'], makeCallbacks());
    expect(() => handle.destroy()).not.toThrow();
  });
});
