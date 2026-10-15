"use client";

// What a placed widget's frame needs from its dashboard (UR.5.3, UR.5.4): the range it may be
// given, the dashboard's own range, how to set the override, and whether it can be expanded.
// A widget rendered outside a dashboard has no runtime: it follows the 24 h default, shows no
// range control and no Expand, which is how the Phase 2 widgets render in their own tests.

import { createContext, useContext } from "react";

import type { WidgetRange } from "./types";

export const DEFAULT_RANGE: WidgetRange = "24h";

export const RANGE_TEXT: Record<WidgetRange, string> = { "1h": "1 h", "24h": "24 h", "7d": "7 d" };

export interface WidgetRuntime {
  /** The presets the widget declares; null when its reads take no window. */
  rangeOptions: readonly WidgetRange[] | null;
  /** The widget's own range; null follows the dashboard. */
  override: WidgetRange | null;
  dashboardRange: WidgetRange;
  setRange: (range: WidgetRange) => void;
  /** Opens the expand dialog from this trigger; null when the widget is not expandable here. */
  onExpand: ((trigger: HTMLElement) => void) | null;
  /** True inside the expand dialog. */
  expanded: boolean;
  /**
   * Opens the configure drawer from this trigger; set only while customizing. A widget whose
   * subject is not chosen, or no longer exists, offers it; in read mode it only says so.
   */
  onConfigure?: ((trigger: HTMLElement) => void) | null;
}

export const WidgetRuntimeContext = createContext<WidgetRuntime | null>(null);

/** The widget's override, else the dashboard's range. */
export function effectiveRange(
  override: WidgetRange | null | undefined,
  dashboard: WidgetRange,
): WidgetRange {
  return override ?? dashboard;
}

/** The range a widget reads: its own override, else its dashboard's, else 24 h. */
export function useEffectiveRange(override: WidgetRange | null | undefined): WidgetRange {
  const runtime = useContext(WidgetRuntimeContext);
  return effectiveRange(override, runtime?.dashboardRange ?? DEFAULT_RANGE);
}

/** What the widget component receives besides `id` and `config`. */
export interface RangedProps {
  range?: WidgetRange | null;
  expanded?: boolean;
}

/** True inside the expand dialog: a table then shows every row its read returned. */
export function useExpanded(prop?: boolean): boolean {
  const runtime = useContext(WidgetRuntimeContext);
  return prop ?? runtime?.expanded ?? false;
}
