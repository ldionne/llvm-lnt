// v5 API response types

export interface FieldInfo {
  name: string;
  type: string;
  display_name: string | null;
  unit: string | null;
  unit_abbrev: string | null;
  bigger_is_better: boolean | null;
}

export interface CommitSummary {
  commit: string;
  ordinal: number | null;
  tag: string | null;
  fields: Record<string, string>;
}

export interface CommitDetail {
  commit: string;
  ordinal: number | null;
  tag: string | null;
  fields: Record<string, string>;
  previous_commit: CommitNeighbor | null;
  next_commit: CommitNeighbor | null;
}

export interface CommitNeighbor {
  commit: string;
  ordinal: number | null;
  tag: string | null;
  link: string;
}

export interface CommitResolveResponse {
  results: Record<string, CommitSummary>;
  not_found: string[];
}

export interface RunInfo {
  uuid: string;
  commit: string;
  submitted_at: string | null;
  run_parameters: Record<string, string>;
}

export interface RunDetail {
  uuid: string;
  commit: string;
  submitted_at: string | null;
  run_parameters: Record<string, string>;
}

export interface SampleInfo {
  test: string;
  metrics: Record<string, number | null>;
}

// Profile types

export interface ProfileListItem {
  test: string;
  uuid: string;
}

export interface ProfileMetadata {
  uuid: string;
  test: string;
  run_uuid: string;
  counters: Record<string, number>;
  disassembly_format: string;
}

export interface ProfileFunctionInfo {
  name: string;
  counters: Record<string, number>;
  length: number;
}

export interface ProfileInstruction {
  address: number;
  counters: Record<string, number>;
  text: string;
}

export interface ProfileFunctionDetail {
  name: string;
  counters: Record<string, number>;
  disassembly_format: string;
  instructions: ProfileInstruction[];
}

export interface FieldChangeInfo {
  uuid: string;
  test: string | null;
  metric: string | null;
  old_value: number;
  new_value: number;
  start_commit: string | null;
  end_commit: string | null;
}

export interface QueryDataPoint {
  test: string;
  metric: string;
  value: number;
  commit: string;
  ordinal: number | null;
  tag: string | null;
  run_uuid: string;
  submitted_at: string | null;
}

export interface CursorPaginated<T> {
  items: T[];
  cursor: {
    next: string | null;
    previous: string | null;
  };
}

export interface OffsetPaginated<T> {
  items: T[];
  total: number;
  cursor: {
    next: string | null;
    previous: string | null;
  };
}

// App state

export type AggFn = 'median' | 'mean' | 'min' | 'max';
export type SortDir = 'asc' | 'desc';
export type SortCol = 'test' | 'value_a' | 'value_b' | 'delta' | 'delta_pct' | 'ratio' | 'status';

export interface SideSelection {
  suite: string;
  commit: string;
  params: Record<string, string>;
  runs: string[];     // UUIDs
  runAgg: AggFn;
}

export interface NoiseKnob {
  enabled: boolean;
  value: number;
}

export interface NoiseConfig {
  pct: NoiseKnob;    // Delta % below threshold
  pval: NoiseKnob;   // P-value above threshold
  floor: NoiseKnob;  // Absolute value below floor
}

export interface NoiseReason {
  knob: 'pct' | 'pval' | 'floor';
  message: string;
}

export interface AppState {
  sideA: SideSelection;
  sideB: SideSelection;
  metric: string;
  sampleAgg: AggFn;
  noiseConfig: NoiseConfig;
  sort: SortCol;
  sortDir: SortDir;
  testFilter: string;
  hideNoise: boolean;
}

// Comparison results

export type RowStatus = 'improved' | 'regressed' | 'unchanged' | 'noise' | 'missing' | 'na';

export interface ComparisonRow {
  test: string;
  valueA: number | null;
  valueB: number | null;
  delta: number | null;
  deltaPct: number | null;
  ratio: number | null;
  status: RowStatus;
  sidePresent: 'both' | 'a_only' | 'b_only';
  noiseReasons: NoiseReason[];
}

// Admin types

export interface APIKeyItem {
  prefix: string;
  name: string;
  scope: string;
  created_at: string;
  last_used_at: string | null;
  is_active: boolean;
}

export interface APIKeyCreateResponse {
  key: string;
  prefix: string;
  scope: string;
}

export interface TestSuiteInfo {
  name: string;
  schema: {
    metrics: FieldInfo[];
    commit_fields: Array<{ name: string; type: string; display?: boolean }>;
  };
}

// Regression types

export type RegressionState =
  | 'detected'
  | 'active'
  | 'not_to_be_fixed'
  | 'fixed'
  | 'false_positive';

export interface RegressionIndicator {
  uuid: string;
  run_uuid: string | null;
  test: string | null;
  metric: string;
}

/** Regression as returned by GET /regressions (list endpoint). */
export interface RegressionListItem {
  uuid: string;
  title: string | null;
  bug: string | null;
  state: RegressionState;
  commit: string | null;
  run_count: number;
  test_count: number;
}

/** Regression as returned by GET /regressions/{uuid} (detail endpoint). */
export interface RegressionDetail {
  uuid: string;
  title: string | null;
  bug: string | null;
  notes: string | null;
  state: RegressionState;
  commit: string | null;
  indicators: RegressionIndicator[];
}

// Parameter query types

export interface ParameterKeyItem {
  key: string;
}

export interface ParameterValueItem {
  value: string;
}

// Dashboard types

export interface DashboardCard {
  id?: number;
  position: number;
  params: Record<string, string>;
  metric: string;
  last_n: number;
}

// POST /samples request/response types

export interface SamplesQueryRequest {
  params?: Record<string, string | string[]>;
  test?: string[];
  metric?: string;
  commit?: string;
  run?: string;
  sort?: string;
  limit?: number;
  cursor?: string;
  submitted_before?: string;
  submitted_after?: string;
}

export interface SamplesQueryItem {
  test: string;
  value?: number;
  metrics?: Record<string, number | null>;
  commit: string;
  ordinal: number | null;
  tag: string | null;
  run_uuid: string;
  submitted_at: string | null;
}

// Search chip types (used by components/search-chips.ts)

export interface ParamChip {
  key: string;
  value: string;
}

/**
 * Encode a parameter query dict to the compact URL format.
 * e.g. {compiler: "clang-21", os: "linux"} => "compiler:clang-21,os:linux"
 */
export function encodeParamQuery(params: Record<string, string>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k}:${v}`)
    .join(',');
}

/**
 * Decode the compact URL format to a parameter query dict.
 * e.g. "compiler:clang-21,os:linux" => {compiler: "clang-21", os: "linux"}
 */
export function decodeParamQuery(encoded: string): Record<string, string> {
  if (!encoded) return {};
  const result: Record<string, string> = {};
  for (const part of encoded.split(',')) {
    const idx = part.indexOf(':');
    if (idx > 0) {
      result[part.slice(0, idx)] = part.slice(idx + 1);
    }
  }
  return result;
}

/**
 * Format a parameter query dict as a compact display string.
 * e.g. {compiler: "clang-21", os: "linux"} => "compiler:clang-21, os:linux"
 */
export function formatParamQuery(params: Record<string, string>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k}:${v}`)
    .join(', ');
}

/**
 * Convert a params dict to API query param format (param.X=Y entries).
 */
export function paramsToApiQuery(params: Record<string, string>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    result[`param.${k}`] = v;
  }
  return result;
}
