import type { SideSelection } from './types';
import { el, matchesFilter, updateFilterValidation } from './utils';

// Per-side commit picker references for enabling/disabling from search chips
let commitPickerA: CommitPickerHandle | null = null;
let commitPickerB: CommitPickerHandle | null = null;

/** Shared state that the combobox module reads but does not own. */
export interface ComboboxContext {
  /** Get per-side commit values and display map. */
  getCommitData: (side: 'a' | 'b') => {
    cachedCommitValues: string[];
    displayMap?: Map<string, string>;
  };
  /** Get the testsuite name for a given side. */
  getSuiteName: (side: 'a' | 'b') => string;
  getSideState: (side: 'a' | 'b') => {
    selection: SideSelection;
    setSide: (partial: Partial<SideSelection>) => void;
    label: string;
  };
  /** Fetch commits filtered by params for a side. */
  fetchCommitsForParams: (side: 'a' | 'b', params: Record<string, string>) => Promise<void>;
}

/** Reset per-panel mutable state.  Call this at the start of renderSelectionPanel. */
export function resetComboboxState(): void {
  commitPickerA = null;
  commitPickerB = null;
}

/** Re-resolve the commit picker's display value (e.g. after commitFieldsCache is populated). */
export function refreshCommitDisplay(side: 'a' | 'b', rawCommit: string): void {
  const picker = side === 'a' ? commitPickerA : commitPickerB;
  if (!picker || !rawCommit) return;
  picker.setValue(rawCommit);
}

/** Set the commit input to one of three states: no params selected, loading commits, or ready. */
function setCommitInputState(
  input: HTMLInputElement | null,
  state: 'no-params' | 'loading' | 'ready',
  value?: string,
): void {
  if (!input) return;
  if (state === 'no-params') {
    input.disabled = true;
    input.placeholder = 'Add parameters first';
  } else if (state === 'loading') {
    input.disabled = true;
    input.placeholder = 'Loading commits...';
  } else {
    input.disabled = false;
    input.placeholder = 'Type to search commits...';
  }
  if (value !== undefined) input.value = value;
}

/** Get the commit picker for a side. */
export function getCommitPicker(side: 'a' | 'b'): CommitPickerHandle | null {
  return side === 'a' ? commitPickerA : commitPickerB;
}

/** Update commit input state externally (used by selection.ts after param changes). */
export function updateCommitInputState(
  side: 'a' | 'b',
  state: 'no-params' | 'loading' | 'ready',
  value?: string,
): void {
  const picker = side === 'a' ? commitPickerA : commitPickerB;
  setCommitInputState(picker?.input ?? null, state, value);
}

function setAriaExpanded(wrapper: HTMLElement, expanded: boolean): void {
  wrapper.setAttribute('aria-expanded', String(expanded));
}

function setupComboboxKeyboard(
  input: HTMLInputElement,
  dropdown: HTMLUListElement,
  wrapper: HTMLElement,
): void {
  input.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const first = dropdown.querySelector<HTMLLIElement>('li');
      if (first) first.focus();
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('open');
      setAriaExpanded(wrapper, false);
    }
  });

  dropdown.addEventListener('keydown', (e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.tagName !== 'LI') return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = target.nextElementSibling as HTMLElement | null;
      if (next) next.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = target.previousElementSibling as HTMLElement | null;
      if (prev) prev.focus();
      else input.focus();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      target.click();
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('open');
      setAriaExpanded(wrapper, false);
      input.focus();
    }
  });
}

// ---------------------------------------------------------------------------
// createCommitPicker — reusable commit combobox
// ---------------------------------------------------------------------------

export interface CommitPickerOptions {
  id: string;
  /** Called on each dropdown open/filter to get the current commit data.
   *  Lazy evaluation ensures data fetched after picker creation is visible. */
  getCommitData: () => { values: string[]; displayMap?: Map<string, string> };
  initialValue?: string;
  placeholder?: string;
  onSelect: (value: string) => void;
}

export interface CommitPickerHandle {
  element: HTMLElement;
  input: HTMLInputElement;
  setValue: (raw: string) => void;
  destroy: () => void;
}

export function createCommitPicker(opts: CommitPickerOptions): CommitPickerHandle {
  const dropdownId = `commit-dropdown-${opts.id}`;
  const wrapper = el('div', {
    class: 'combobox',
    role: 'combobox',
    'aria-expanded': 'false',
    'aria-haspopup': 'listbox',
  });
  const input = el('input', {
    type: 'text',
    placeholder: opts.placeholder || 'Type to search commits...',
    class: 'combobox-input',
    role: 'searchbox',
    'aria-autocomplete': 'list',
    'aria-controls': dropdownId,
  });
  const dropdown = el('ul', { class: 'combobox-dropdown', role: 'listbox', id: dropdownId });
  wrapper.append(input, dropdown);

  // Prevent blur from firing when clicking a dropdown item
  dropdown.addEventListener('mousedown', (e) => e.preventDefault());

  // Keyboard navigation
  setupComboboxKeyboard(input, dropdown, wrapper);

  function resolveDisplay(raw: string): string {
    const { displayMap } = opts.getCommitData();
    return displayMap?.get(raw) ?? raw;
  }

  // Set initial value (display map may not be loaded yet — falls back to raw)
  if (opts.initialValue) {
    input.value = resolveDisplay(opts.initialValue);
  }

  function showDropdown(filter: string): void {
    const { values, displayMap } = opts.getCommitData();
    const matches = filter
      ? values.filter(v => {
          if (matchesFilter(v, filter)) return true;
          const display = displayMap?.get(v);
          return display ? matchesFilter(display, filter) : false;
        })
      : values;
    const limited = matches.slice(0, 100);

    dropdown.replaceChildren();
    for (const v of limited) {
      const displayText = displayMap?.get(v) ?? v;
      const li = el('li', { class: 'combobox-item', role: 'option', tabindex: '-1' }, displayText);
      li.addEventListener('click', () => {
        input.value = displayText;
        input.classList.remove('combobox-invalid');
        dropdown.classList.remove('open');
        setAriaExpanded(wrapper, false);
        opts.onSelect(v);
      });
      dropdown.append(li);
    }
    const isOpen = limited.length > 0;
    dropdown.classList.toggle('open', isOpen);
    setAriaExpanded(wrapper, isOpen);

    // Show/hide validation halo based on whether any commits match
    if (input.value.trim() && matches.length === 0) {
      input.classList.add('combobox-invalid');
    } else {
      input.classList.remove('combobox-invalid');
    }
  }

  /** Check if a value is an exact match against available commit values. */
  function isValidCommit(raw: string): boolean {
    const { values } = opts.getCommitData();
    return values.includes(raw);
  }

  input.addEventListener('focus', () => showDropdown(input.value));
  input.addEventListener('input', () => {
    updateFilterValidation(input);
    showDropdown(input.value);
  });
  input.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (input.classList.contains('combobox-invalid')) return;
      const raw = input.value.replace(/\s*\(.*\)$/, '').trim();
      if (!raw) return;
      if (!isValidCommit(raw)) {
        input.classList.add('combobox-invalid');
        return;
      }
      dropdown.classList.remove('open');
      setAriaExpanded(wrapper, false);
      opts.onSelect(raw);
    }
  });
  input.addEventListener('blur', (e: FocusEvent) => {
    if (wrapper.contains(e.relatedTarget as Node)) return;
    dropdown.classList.remove('open');
    setAriaExpanded(wrapper, false);
  });
  input.addEventListener('change', () => {
    // Strip any trailing parenthetical if present
    if (input.classList.contains('combobox-invalid')) return;
    const raw = input.value.replace(/\s*\(.*\)$/, '').trim();
    if (!raw) { opts.onSelect(raw); return; }
    if (!isValidCommit(raw)) {
      input.classList.add('combobox-invalid');
      return;
    }
    opts.onSelect(raw);
  });

  return {
    element: wrapper,
    input,
    setValue: (raw: string) => { input.value = resolveDisplay(raw); },
    destroy: () => { /* no internal fetches to abort */ },
  };
}

// ---------------------------------------------------------------------------
// createCommitCombobox — Compare page wrapper around createCommitPicker
// ---------------------------------------------------------------------------

export function createCommitCombobox(
  side: 'a' | 'b',
  setSide: (partial: Partial<SideSelection>) => void,
  onCommitChange: () => void,
  ctx: ComboboxContext,
): HTMLElement {
  const { selection } = ctx.getSideState(side);

  const picker = createCommitPicker({
    id: `commit-${side}`,
    getCommitData: () => {
      const { cachedCommitValues, displayMap } = ctx.getCommitData(side);
      return { values: cachedCommitValues, displayMap };
    },
    initialValue: selection.commit,
    placeholder: 'Type to search commits...',
    onSelect: (value) => {
      setSide(value ? { commit: value } : { commit: '', runs: [] });
      onCommitChange();
    },
  });

  // Store refs for interaction
  if (side === 'a') commitPickerA = picker;
  else commitPickerB = picker;

  // Disable commit input until params are set.
  const hasParams = Object.keys(selection.params).length > 0;
  setCommitInputState(picker.input, hasParams ? 'loading' : 'no-params');

  return picker.element;
}
