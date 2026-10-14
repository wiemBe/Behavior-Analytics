// Layout geometry (UR.5.3): pure functions, no DOM.
//
// Only the 12-column layout is stored. The 6- and 1-column layouts are derived from it, so a
// narrow window shows the same dashboard without a second layout to keep in step.

import { GRID_COLUMNS, type LayoutWidget } from "./types";

export type GridColumns = 12 | 6 | 1;

/** The container width from which the grid has 12 columns (and can be edited), in px. */
export const WIDE_MIN_PX = 900;
/** The container width from which the grid has 6 columns, in px. */
export const MEDIUM_MIN_PX = 600;

/**
 * Columns follow the container's width, not the viewport's, because the sidebar takes a share
 * of it. 900 px is the threshold UI-2.5 settled so that a 1280 px viewport (about 984 px of
 * content) keeps 12 columns and 1024 px (about 728 px) reflows.
 */
export function columnsForWidth(widthPx: number): GridColumns {
  if (widthPx >= WIDE_MIN_PX) return 12;
  if (widthPx >= MEDIUM_MIN_PX) return 6;
  return 1;
}

function reading(a: LayoutWidget, b: LayoutWidget): number {
  return a.y - b.y || a.x - b.x;
}

/**
 * The layout for `cols` columns from the stored 12-column one. Widgets keep their reading order
 * (top to bottom, then left to right) and their heights. A widget narrower than half the grid
 * takes half of the narrow grid, anything wider takes all of it, and widgets wrap in order, so
 * nothing overlaps and `x + w` never exceeds `cols`.
 */
export function deriveLayout(lg: readonly LayoutWidget[], cols: GridColumns): LayoutWidget[] {
  const ordered = [...lg].sort(reading);
  if (cols === GRID_COLUMNS) return ordered.map((w) => ({ ...w }));
  const half = Math.max(1, cols / 2);
  const out: LayoutWidget[] = [];
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const widget of ordered) {
    const w = widget.w < GRID_COLUMNS / 2 ? half : cols;
    if (x + w > cols) {
      y += rowH;
      x = 0;
      rowH = 0;
    }
    out.push({ ...widget, x, y, w });
    x += w;
    rowH = Math.max(rowH, widget.h);
  }
  return out;
}

function overlaps(a: LayoutWidget, x: number, y: number, w: number, h: number): boolean {
  return a.x < x + w && x < a.x + a.w && a.y < y + h && y < a.y + a.h;
}

/**
 * The first free spot for a w by h widget: the lowest row, then the leftmost column, where it
 * overlaps nothing. A dashboard has no height limit, so there is always one.
 */
export function firstFreeSpot(
  widgets: readonly LayoutWidget[],
  w: number,
  h: number,
): { x: number; y: number } {
  const bottom = widgets.reduce((max, a) => Math.max(max, a.y + a.h), 0);
  for (let y = 0; y <= bottom; y++) {
    for (let x = 0; x + w <= GRID_COLUMNS; x++) {
      if (!widgets.some((a) => overlaps(a, x, y, w, h))) return { x, y };
    }
  }
  return { x: 0, y: bottom };
}

/** A new widget id, `^w_[a-z0-9]{4,12}$`, not among `taken`. */
export function newWidgetId(taken: ReadonlySet<string>, random: () => number = Math.random): string {
  for (;;) {
    const id = `w_${Math.floor(random() * 36 ** 6)
      .toString(36)
      .padStart(6, "0")}`;
    if (!taken.has(id)) return id;
  }
}
