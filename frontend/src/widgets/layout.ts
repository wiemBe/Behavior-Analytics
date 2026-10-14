// Layout geometry (UR.5.3): pure functions, no DOM.
//
// Only the 12-column layout is stored. The 6- and 1-column layouts are derived from it, so a
// narrow window shows the same dashboard without a second layout to keep in step.

import { GRID_COLUMNS, type LayoutWidget, type WidgetSize } from "./types";

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

function widgetsOverlap(a: LayoutWidget, b: LayoutWidget): boolean {
  return overlaps(a, b.x, b.y, b.w, b.h);
}

/**
 * Vertical compaction shared by removal and the keyboard editor. `lockedId` is the item the
 * analyst just changed: it stays in the requested cell while every other item rises as far as
 * it can without a collision. This is the keyboard equivalent of the edit grid's vertical
 * compactor.
 */
export function compactLayout(
  widgets: readonly LayoutWidget[],
  lockedId: string | null = null,
): LayoutWidget[] {
  const copies = widgets.map((widget) => ({ ...widget }));
  const locked = lockedId ? copies.find((widget) => widget.id === lockedId) : undefined;
  const ordered = copies.filter((widget) => widget.id !== lockedId).sort(reading);
  const placed = locked ? [locked] : [];

  for (const widget of ordered) {
    widget.x = Math.max(0, Math.min(widget.x, GRID_COLUMNS - widget.w));
    widget.y = Math.max(0, widget.y);
    while (widget.y > 0 && !placed.some((other) => overlaps(other, widget.x, widget.y - 1, widget.w, widget.h))) {
      widget.y -= 1;
    }
    while (placed.some((other) => widgetsOverlap(other, widget))) {
      widget.y = Math.max(
        widget.y + 1,
        ...placed.filter((other) => widgetsOverlap(other, widget)).map((other) => other.y + other.h),
      );
    }
    placed.push(widget);
  }

  const byId = new Map(placed.map((widget) => [widget.id, widget]));
  return widgets.map((widget) => ({ ...byId.get(widget.id)! }));
}

function settleChange(widgets: LayoutWidget[], changedId: string): LayoutWidget[] {
  if (!widgets.some((widget) => widget.id === changedId)) return widgets;
  // The changed item owns its requested cells; the remaining layout compacts around it.
  return compactLayout(widgets, changedId);
}

export type MoveDirection = "left" | "right" | "up" | "down";
export type ResizeDirection = "wider" | "narrower" | "taller" | "shorter";

/** Move one widget by one cell, constrained to the 12-column stored layout. */
export function moveWidget(
  widgets: readonly LayoutWidget[],
  id: string,
  direction: MoveDirection,
): LayoutWidget[] {
  const next = widgets.map((widget) => ({ ...widget }));
  const widget = next.find((candidate) => candidate.id === id);
  if (!widget) return next;

  const dx = direction === "left" ? -1 : direction === "right" ? 1 : 0;
  const dy = direction === "up" ? -1 : direction === "down" ? 1 : 0;
  const x = Math.max(0, Math.min(widget.x + dx, GRID_COLUMNS - widget.w));
  const y = Math.max(0, widget.y + dy);
  if (x === widget.x && y === widget.y) return next;
  widget.x = x;
  widget.y = y;
  return settleChange(next, id);
}

/** Resize one widget by one cell, respecting its registry bounds and the 12-column edge. */
export function resizeWidget(
  widgets: readonly LayoutWidget[],
  id: string,
  direction: ResizeDirection,
  size: WidgetSize,
): LayoutWidget[] {
  const next = widgets.map((widget) => ({ ...widget }));
  const widget = next.find((candidate) => candidate.id === id);
  if (!widget) return next;

  const maxW = Math.min(size.maxW ?? GRID_COLUMNS, GRID_COLUMNS - widget.x);
  const maxH = size.maxH ?? Number.POSITIVE_INFINITY;
  const w =
    direction === "wider"
      ? Math.min(widget.w + 1, maxW)
      : direction === "narrower"
        ? Math.max(widget.w - 1, size.minW)
        : widget.w;
  const h =
    direction === "taller"
      ? Math.min(widget.h + 1, maxH)
      : direction === "shorter"
        ? Math.max(widget.h - 1, size.minH)
        : widget.h;
  if (w === widget.w && h === widget.h) return next;
  widget.w = w;
  widget.h = h;
  return settleChange(next, id);
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
