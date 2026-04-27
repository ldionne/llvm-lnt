// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { decodeGraphState, encodeGraphState, replaceGraphUrl } from '../../../pages/graph/state';
import type { GraphState } from '../../../pages/graph/state';

function makeDefault(): GraphState {
  return {
    suite: '',
    traces: [],
    machines: [],
    metric: '',
    testFilter: '',
    runAgg: 'median',
    sampleAgg: 'median',
    baselines: [],
    regressionMode: 'off',
  };
}

describe('decodeGraphState', () => {
  it('returns defaults for empty search string', () => {
    expect(decodeGraphState('')).toEqual(makeDefault());
  });

  it('returns defaults for "?"', () => {
    expect(decodeGraphState('?')).toEqual(makeDefault());
  });

  it('parses suite and metric', () => {
    const state = decodeGraphState('?suite=nts&metric=exec_time');
    expect(state.suite).toBe('nts');
    expect(state.metric).toBe('exec_time');
  });

  it('parses single trace', () => {
    const state = decodeGraphState('?trace=compiler:clang');
    expect(state.traces).toEqual([{ compiler: 'clang' }]);
  });

  it('parses multiple traces (repeated param)', () => {
    const state = decodeGraphState('?trace=compiler:clang&trace=os:linux&trace=arch:x86');
    expect(state.traces).toEqual([{ compiler: 'clang' }, { os: 'linux' }, { arch: 'x86' }]);
  });

  it('filters empty trace values', () => {
    const state = decodeGraphState('?trace=compiler:clang&trace=&trace=os:linux');
    expect(state.traces).toEqual([{ compiler: 'clang' }, { os: 'linux' }]);
  });

  it('parses legacy machine param', () => {
    const state = decodeGraphState('?machine=host1');
    expect(state.machines).toEqual(['host1']);
  });

  it('parses test_filter', () => {
    const state = decodeGraphState('?test_filter=benchmark');
    expect(state.testFilter).toBe('benchmark');
  });

  it('parses aggregation functions', () => {
    const state = decodeGraphState('?run_agg=mean&sample_agg=max');
    expect(state.runAgg).toBe('mean');
    expect(state.sampleAgg).toBe('max');
  });

  it('defaults invalid aggregation to median', () => {
    const state = decodeGraphState('?run_agg=invalid&sample_agg=bogus');
    expect(state.runAgg).toBe('median');
    expect(state.sampleAgg).toBe('median');
  });

  it('parses single baseline', () => {
    const state = decodeGraphState('?baseline=nts::compiler:clang::abc123');
    expect(state.baselines).toEqual([
      { suite: 'nts', params: { compiler: 'clang' }, commit: 'abc123' },
    ]);
  });

  it('parses multiple baselines', () => {
    const state = decodeGraphState(
      '?baseline=nts::compiler:clang::c1&baseline=other::os:linux::c2',
    );
    expect(state.baselines).toHaveLength(2);
    expect(state.baselines[0]).toEqual({ suite: 'nts', params: { compiler: 'clang' }, commit: 'c1' });
    expect(state.baselines[1]).toEqual({ suite: 'other', params: { os: 'linux' }, commit: 'c2' });
  });

  it('skips malformed baselines', () => {
    const state = decodeGraphState(
      '?baseline=nts::compiler:clang::c1&baseline=bad_format&baseline=::::',
    );
    expect(state.baselines).toHaveLength(1);
    expect(state.baselines[0]).toEqual({ suite: 'nts', params: { compiler: 'clang' }, commit: 'c1' });
  });

  it('parses baseline with empty params', () => {
    const state = decodeGraphState('?baseline=nts::::abc123');
    expect(state.baselines).toEqual([
      { suite: 'nts', params: {}, commit: 'abc123' },
    ]);
  });

  it('parses regressionMode', () => {
    expect(decodeGraphState('?regressions=active').regressionMode).toBe('active');
    expect(decodeGraphState('?regressions=all').regressionMode).toBe('all');
    expect(decodeGraphState('?regressions=off').regressionMode).toBe('off');
  });

  it('defaults invalid regressionMode to off', () => {
    expect(decodeGraphState('?regressions=bogus').regressionMode).toBe('off');
  });

  it('parses a full URL with all params', () => {
    const state = decodeGraphState(
      '?suite=nts&trace=compiler:clang&trace=os:linux&metric=exec_time&test_filter=bench' +
      '&run_agg=mean&sample_agg=min&baseline=nts::compiler:clang::c1&regressions=active',
    );
    expect(state.suite).toBe('nts');
    expect(state.traces).toEqual([{ compiler: 'clang' }, { os: 'linux' }]);
    expect(state.metric).toBe('exec_time');
    expect(state.testFilter).toBe('bench');
    expect(state.runAgg).toBe('mean');
    expect(state.sampleAgg).toBe('min');
    expect(state.baselines).toEqual([{ suite: 'nts', params: { compiler: 'clang' }, commit: 'c1' }]);
    expect(state.regressionMode).toBe('active');
  });
});

describe('encodeGraphState', () => {
  it('returns empty string for default state', () => {
    expect(encodeGraphState(makeDefault())).toBe('');
  });

  it('encodes suite and metric', () => {
    const state = { ...makeDefault(), suite: 'nts', metric: 'exec_time' };
    const search = encodeGraphState(state);
    expect(search).toContain('suite=nts');
    expect(search).toContain('metric=exec_time');
  });

  it('encodes multiple traces', () => {
    const state = { ...makeDefault(), traces: [{ compiler: 'clang' }, { os: 'linux' }] };
    const search = encodeGraphState(state);
    expect(search).toContain('trace=compiler%3Aclang');
    expect(search).toContain('trace=os%3Alinux');
  });

  it('omits default aggregation', () => {
    const state = { ...makeDefault(), suite: 'nts' };
    const search = encodeGraphState(state);
    expect(search).not.toContain('run_agg');
    expect(search).not.toContain('sample_agg');
  });

  it('includes non-default aggregation', () => {
    const state = { ...makeDefault(), runAgg: 'mean' as const, sampleAgg: 'max' as const };
    const search = encodeGraphState(state);
    expect(search).toContain('run_agg=mean');
    expect(search).toContain('sample_agg=max');
  });

  it('omits regression mode when off (default)', () => {
    const state = { ...makeDefault(), suite: 'nts' };
    const search = encodeGraphState(state);
    expect(search).not.toContain('regressions');
  });

  it('includes regression mode when not off', () => {
    const state = { ...makeDefault(), regressionMode: 'active' as const };
    const search = encodeGraphState(state);
    expect(search).toContain('regressions=active');
  });

  it('encodes baselines', () => {
    const state = {
      ...makeDefault(),
      baselines: [
        { suite: 'nts', params: { compiler: 'clang' }, commit: 'c1' },
        { suite: 'other', params: { os: 'linux' }, commit: 'c2' },
      ],
    };
    const search = encodeGraphState(state);
    expect(search).toContain('baseline=nts%3A%3Acompiler%3Aclang%3A%3Ac1');
    expect(search).toContain('baseline=other%3A%3Aos%3Alinux%3A%3Ac2');
  });

  it('omits empty suite and metric', () => {
    const search = encodeGraphState(makeDefault());
    expect(search).not.toContain('suite=');
    expect(search).not.toContain('metric=');
  });
});

describe('encode/decode round-trip', () => {
  it('round-trips a full state', () => {
    const original: GraphState = {
      suite: 'nts',
      traces: [{ compiler: 'clang' }, { os: 'linux' }],
      machines: [],
      metric: 'exec_time',
      testFilter: 'bench',
      runAgg: 'mean',
      sampleAgg: 'min',
      baselines: [{ suite: 'nts', params: { compiler: 'clang' }, commit: 'c1' }],
      regressionMode: 'active',
    };
    const encoded = encodeGraphState(original);
    const decoded = decodeGraphState(encoded);
    expect(decoded).toEqual(original);
  });

  it('round-trips default state', () => {
    const original = makeDefault();
    const encoded = encodeGraphState(original);
    const decoded = decodeGraphState(encoded);
    expect(decoded).toEqual(original);
  });

  it('round-trips state with only suite', () => {
    const original = { ...makeDefault(), suite: 'nts' };
    const encoded = encodeGraphState(original);
    const decoded = decodeGraphState(encoded);
    expect(decoded).toEqual(original);
  });
});

describe('replaceGraphUrl', () => {
  it('calls history.replaceState with encoded URL', () => {
    const state = { ...makeDefault(), suite: 'nts', metric: 'exec_time' };
    replaceGraphUrl(state);
    expect(window.location.search).toContain('suite=nts');
    expect(window.location.search).toContain('metric=exec_time');
  });
});
