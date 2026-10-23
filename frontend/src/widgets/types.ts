// The widget system's types (UR.5.1, UR.5.4, UR.5.5).
//
// A widget is a typed entry in one registry; a dashboard is a layout (version 1) of placed
// widgets that name a registry entry by `type`. The layout holds ids, geometry and enumerated
// config only: nothing in it is ever executed (AGENTS.md rule 4).

import type { ComponentType, ReactNode } from "react";

export type WidgetCategory = "KPI" | "Chart" | "Table" | "Health" | "Admin";

/** The per-widget range presets (UR.5.3). */
export type WidgetRange = "1h" | "24h" | "7d";

/** The 12-column grid every stored layout uses (UR.5.3). */
export const GRID_COLUMNS = 12;

/** One candidate of an option read: the id the layout stores, and what the analyst reads. */
export interface ConfigOption {
  id: string;
  label: string;
}

/** What an option read returns: the candidates, and how many exist (may exceed the page). */
export interface ConfigOptionList {
  options: ConfigOption[];
  total: number;
}

/**
 * One existing GET read, in the shape `useApiResource` takes. It returns the read's own payload,
 * so the request is shared with the widgets that read the same path; `options` maps it.
 */
export interface OptionsRead<T = any> {
  key: string;
  fetch: (signal: AbortSignal) => Promise<T>;
  options: (payload: T) => ConfigOptionList;
}

/**
 * Options that come from a read (UI-3.5b): the candidate ids with their labels. A source may
 * depend on another key (a cascade): `read` receives that key's chosen id, and returns null
 * while none is chosen. Changing the parent clears this key.
 */
export interface OptionsSource {
  /** What the options are, plural, for the field's states: "WAF sources". */
  what: string;
  /** Why the read may legitimately return nothing; shown when it does. */
  emptyReason: string;
  /** The config key whose value this read needs, if any. */
  dependsOn?: string;
  /** The text shown while `dependsOn` has no value. */
  needs?: string;
  read: (parent: string | null) => OptionsRead | null;
}

/**
 * A config key is one of two kinds. Static: a pick from a fixed list. From a read: a pick of an
 * id from one existing GET read. Either way the control is a select; the only text field is the
 * client-side filter over a long read, and its text is never stored or sent (AGENTS.md rule 4).
 */
export type ConfigField<V> =
  | { label: string; options: ReadonlyArray<V> }
  | { label: string; from: OptionsSource };

export type ConfigSchema<C> = {
  [K in keyof C]: ConfigField<C[K]>;
};

/** True for a field whose options come from a read. */
export function isReadField(field: ConfigField<unknown>): field is { label: string; from: OptionsSource } {
  return "from" in field;
}

export interface WidgetSize {
  w: number;
  h: number;
  minW: number;
  minH: number;
  maxW?: number;
  maxH?: number;
}

/** What a placed widget's component receives. */
export interface WidgetProps<C> {
  /** The id of the placed widget in its layout. */
  id: string;
  config: C;
  /** The per-widget range override; null follows the dashboard. */
  range: WidgetRange | null;
  /** True inside the expand dialog: the widget may show more of what its read already holds. */
  expanded: boolean;
}

export interface WidgetDef<C = Record<string, never>> {
  /** Stable, `^[a-z0-9_.]{1,64}$`, never renamed. */
  type: string;
  title: string;
  description: string;
  category: WidgetCategory;
  /** Permissions the widget's reads need, e.g. `["read:fleet"]`. */
  requires: string[];
  /** The exact API routes the widget reads (documentation, and a test). */
  sources: string[];
  /** `blocked`: the catalog lists it and cannot add it. */
  status: "live" | "blocked";
  /** Why a `blocked` widget is blocked; absent on a `live` one. */
  blockedBy?: string;
  size: WidgetSize;
  /** Per-widget range presets; null follows the dashboard default. */
  range: null | WidgetRange[];
  /** Charts and tables open in the expand dialog; KPI and health cards do not. */
  expandable: boolean;
  config: ConfigSchema<C>;
  defaultConfig: C;
  /** Where the widget's title leads, or null. */
  drillDown: (cfg: C) => string | null;
  /** Loaded with `next/dynamic`, `ssr: false`. */
  Component: ComponentType<WidgetProps<C>>;
}

/** A widget definition of any config type, as the registry holds it. */
export type AnyWidgetDef = WidgetDef<any>;

/**
 * What a widget frame can show (UR.5.4). `INSUFFICIENT_DATA`, `PARTIAL` and `TRUNCATED` are
 * qualifiers a widget attaches to an `ok` state, not kinds of their own.
 */
export type WidgetState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T; asOf: string | null }
  | { kind: "empty"; reason: ReactNode; at?: string }
  | { kind: "no_data"; reason: ReactNode; at?: string }
  | { kind: "not_collected"; reason: ReactNode; at?: string }
  | { kind: "stale"; data: T; asOf: string; ageSeconds: number }
  | { kind: "error"; action: string }
  | { kind: "forbidden"; permission: string };

/** One placed widget of a layout (UR.5.5). */
export interface LayoutWidget {
  /** `^w_[a-z0-9]{4,12}$`, unique in the layout. */
  id: string;
  type: string;
  x: number;
  y: number;
  w: number;
  h: number;
  range: WidgetRange | null;
  config: Record<string, unknown>;
}

/** Layout JSON, version 1 (UR.5.5). */
export interface DashboardLayout {
  v: 1;
  range: WidgetRange;
  widgets: LayoutWidget[];
}

/** A template is a versioned layout that lives next to the registry (UR.5.7). */
export interface DashboardTemplate {
  key: string;
  version: number;
  name: string;
  layout: DashboardLayout;
}
