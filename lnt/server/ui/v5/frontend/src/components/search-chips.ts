// components/search-chips.ts — Two-phase autocomplete parameter search chips.
// Phase 1: key prefix search via getRunParameters(?search=)
// Phase 2: value search via getRunParameterValues(key, ?search=)

import { el } from '../utils';
import { getRunParameters, getRunParameterValues } from '../api';
import type { ParamChip } from '../types';

export interface SearchChipsOptions {
  testsuite: string;
  initialChips?: ParamChip[];
  placeholder?: string;
  disabled?: boolean;
  onChange: (chips: ParamChip[]) => void;
}

export interface SearchChipsHandle {
  element: HTMLElement;
  getChips(): ParamChip[];
  setChips(chips: ParamChip[]): void;
  setDisabled(disabled: boolean): void;
  destroy(): void;
}

export function createSearchChips(opts: SearchChipsOptions): SearchChipsHandle {
  const chips: ParamChip[] = opts.initialChips ? [...opts.initialChips] : [];
  let abortCtrl: AbortController | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let phase: 'key' | 'value' = 'key';
  let selectedKey = '';

  const wrapper = el('div', { class: 'combobox', style: 'position: relative' });
  const inputArea = el('div', { class: 'search-chips-input' });
  const textInput = el('input', {
    type: 'text',
    class: 'search-chips-text-input',
    placeholder: opts.placeholder || 'Add parameter filter...',
    autocomplete: 'off',
  }) as HTMLInputElement;
  const dropdown = el('ul', { class: 'search-chips-dropdown' });

  inputArea.append(textInput);
  wrapper.append(inputArea, dropdown);

  if (opts.disabled) {
    textInput.disabled = true;
    textInput.placeholder = 'Select a suite first';
  }

  // Click on the container focuses the input
  inputArea.addEventListener('click', () => textInput.focus());

  // Prevent dropdown clicks from blurring
  dropdown.addEventListener('mousedown', (e) => e.preventDefault());

  function renderChipElements(): void {
    // Remove existing chip elements
    const existing = inputArea.querySelectorAll('.search-chip');
    existing.forEach(c => c.remove());

    // Add chip elements before the input
    for (const chip of chips) {
      const chipEl = el('span', { class: 'search-chip' });
      chipEl.append(
        el('span', { class: 'search-chip-key' }, chip.key + ':'),
        el('span', {}, chip.value),
      );
      const removeBtn = el('button', {
        type: 'button',
        class: 'search-chip-remove',
        'aria-label': `Remove ${chip.key}:${chip.value}`,
      }, '\u00d7');
      removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = chips.findIndex(c => c.key === chip.key && c.value === chip.value);
        if (idx >= 0) chips.splice(idx, 1);
        renderChipElements();
        opts.onChange([...chips]);
      });
      chipEl.append(removeBtn);
      inputArea.insertBefore(chipEl, textInput);
    }
  }

  renderChipElements();

  function closeDropdown(): void {
    dropdown.classList.remove('open');
    dropdown.replaceChildren();
  }

  function resetPhase(): void {
    phase = 'key';
    selectedKey = '';
    textInput.value = '';
    textInput.placeholder = opts.placeholder || 'Add parameter filter...';
    closeDropdown();
  }

  async function showKeySuggestions(search: string): Promise<void> {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();

    try {
      const result = await getRunParameters(opts.testsuite, { search, limit: 25 }, abortCtrl.signal);
      dropdown.replaceChildren();

      if (search && result.items.length === 0) {
        dropdown.append(
          el('li', { class: 'combobox-item', style: 'color: #999; pointer-events: none' }, 'No matching keys'),
        );
      } else {
        const hint = el('li', { class: 'search-chips-phase-hint' }, 'Select a parameter key:');
        dropdown.append(hint);
        for (const item of result.items) {
          const li = el('li', { class: 'combobox-item', tabindex: '-1' }, item.key + ':');
          li.addEventListener('click', () => {
            phase = 'value';
            selectedKey = item.key;
            textInput.value = '';
            textInput.placeholder = `${item.key}: type value...`;
            showValueSuggestions('');
          });
          dropdown.append(li);
        }
      }

      dropdown.classList.toggle('open', dropdown.children.length > 0);
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
    }
  }

  async function showValueSuggestions(search: string): Promise<void> {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();

    try {
      const result = await getRunParameterValues(opts.testsuite, selectedKey, { search, limit: 25 }, abortCtrl.signal);
      dropdown.replaceChildren();

      const hint = el('li', { class: 'search-chips-phase-hint' }, `Values for ${selectedKey}:`);
      dropdown.append(hint);

      if (result.items.length === 0) {
        dropdown.append(
          el('li', { class: 'combobox-item', style: 'color: #999; pointer-events: none' }, 'No matching values'),
        );
      } else {
        for (const item of result.items) {
          const li = el('li', { class: 'combobox-item', tabindex: '-1' }, item.value);
          li.addEventListener('click', () => {
            // Add chip
            const existing = chips.findIndex(c => c.key === selectedKey && c.value === item.value);
            if (existing < 0) {
              chips.push({ key: selectedKey, value: item.value });
              renderChipElements();
              opts.onChange([...chips]);
            }
            resetPhase();
          });
          dropdown.append(li);
        }
      }

      dropdown.classList.toggle('open', dropdown.children.length > 0);
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
    }
  }

  function debouncedSearch(): void {
    if (debounceTimer !== null) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      const text = textInput.value;
      if (phase === 'key') {
        showKeySuggestions(text);
      } else {
        showValueSuggestions(text);
      }
    }, 200);
  }

  textInput.addEventListener('input', debouncedSearch);
  textInput.addEventListener('focus', () => {
    if (phase === 'key') {
      showKeySuggestions(textInput.value);
    } else {
      showValueSuggestions(textInput.value);
    }
  });
  textInput.addEventListener('blur', (e: FocusEvent) => {
    if (wrapper.contains(e.relatedTarget as Node)) return;
    closeDropdown();
  });
  textInput.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (phase === 'value') {
        resetPhase();
      } else {
        closeDropdown();
      }
    } else if (e.key === 'Backspace' && textInput.value === '' && phase === 'key' && chips.length > 0) {
      // Remove last chip on backspace when input is empty
      chips.pop();
      renderChipElements();
      opts.onChange([...chips]);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const first = dropdown.querySelector<HTMLElement>('.combobox-item');
      if (first) first.focus();
    }
  });

  // Keyboard nav in dropdown
  dropdown.addEventListener('keydown', (e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.tagName !== 'LI') return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = target.nextElementSibling as HTMLElement | null;
      if (next && next.classList.contains('combobox-item')) next.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = target.previousElementSibling as HTMLElement | null;
      if (prev && prev.classList.contains('combobox-item')) prev.focus();
      else textInput.focus();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      target.click();
    } else if (e.key === 'Escape') {
      closeDropdown();
      textInput.focus();
    }
  });

  return {
    element: wrapper,

    getChips(): ParamChip[] {
      return [...chips];
    },

    setChips(newChips: ParamChip[]): void {
      chips.length = 0;
      chips.push(...newChips);
      renderChipElements();
    },

    setDisabled(disabled: boolean): void {
      textInput.disabled = disabled;
      if (disabled) {
        textInput.placeholder = 'Select a suite first';
        closeDropdown();
      } else {
        textInput.placeholder = opts.placeholder || 'Add parameter filter...';
      }
    },

    destroy(): void {
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      if (abortCtrl) abortCtrl.abort();
    },
  };
}

/** Convert ParamChip[] to a params dict (Record<string, string>). */
export function chipsToParams(chips: ParamChip[]): Record<string, string> {
  const params: Record<string, string> = {};
  for (const chip of chips) {
    params[chip.key] = chip.value;
  }
  return params;
}

/** Convert a params dict to ParamChip[]. */
export function paramsToChips(params: Record<string, string>): ParamChip[] {
  return Object.entries(params).map(([key, value]) => ({ key, value }));
}
