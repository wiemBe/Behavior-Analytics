"use client";

// One shared visibility gate for the read grid (UR.8.3): a widget body is mounted only once its
// box has come near the viewport, and stays mounted once it has. The read grid draws every box at
// once — so the page keeps its layout — but an off-screen chart is never created, because its
// component is not rendered until the observer answers. Customize mode does not use this hook: it
// mounts every widget, because dragging needs the real sizes.
//
// Which boxes start deferred is decided in the first render, from the layout, with no DOM read.
// The grid knows the box's row (`y`) and the row pitch (`--dash-row` plus `--dash-gap`: the
// lengths `dashboard.css` lays the 12-column grid out with). A box whose top is inside the
// viewport plus one viewport of margin starts in view, so its body is in that first commit. It
// has to be. A widget's code arrives through `next/dynamic`, and a body whose first render lands
// in a later commit does not appear until React's reveal throttle has run (round 1 measured
// about 250 ms). Boxes below that line wait for the observer.
//
// The page header sits above the grid and this estimate does not subtract it, so the line is a
// few rows eager: an on-screen box is never deferred, and a box just under the margin may mount
// with the first screen. `h` is not an input. A cell only extends downward from its top, so a
// top below the margin is the whole cell below it. A measurement after the first render cannot
// move a body into that commit, so there is no layout effect.
//
// Where the layout cannot be read — no `window` — nothing is deferred. Where
// `IntersectionObserver` does not exist (the test environment, an old browser) nothing is
// deferred either. Both are the grid's behaviour before this hook.
//
// The observer disconnects as soon as it has answered, so it fires once per element. React's
// StrictMode double-invocation is harmless: the hook only ever moves `false` to `true`, and the
// second observer is disconnected by the first cleanup.

import { useEffect, useRef, useState, type RefObject } from "react";

/** About one viewport of slack, the same margin the observer is given. */
const MARGIN_VIEWPORTS = 1;

/** The observer's margin, in the form `IntersectionObserver` takes. */
const ROOT_MARGIN = `${MARGIN_VIEWPORTS * 100}% 0px`;

/**
 * `--dash-row` in tokens.css. A 12-column grid row is this tall (`grid-auto-rows`).
 * The hook's test reads the stylesheet and pins this number, so the two cannot drift quietly.
 */
const ROW_PX = 24;

/** `--dash-gap` in tokens.css (`--widget-gap`, which is `--sp-3`). The gap between rows. */
const GAP_PX = 12;

/**
 * From one row's top to the next. `dashboard.css` sets `grid-auto-rows: var(--dash-row)` and
 * `gap: var(--dash-gap)`, so row `y` starts at `y` times this pitch.
 */
export const ROW_PITCH_PX = ROW_PX + GAP_PX;

/**
 * Whether a cell whose top is row `y` starts inside the viewport plus the margin.
 * `viewportHeight` is `window.innerHeight`, or null when there is no window (the server):
 * then the cell is in view, and the grid mounts every body.
 */
export function cellStartsInView(y: number, viewportHeight: number | null): boolean {
  if (viewportHeight === null || !(viewportHeight > 0)) return true;
  return y * ROW_PITCH_PX < viewportHeight * (1 + MARGIN_VIEWPORTS);
}

/**
 * A ref to put on the observed box, and whether it has ever come near the viewport. `y` is the
 * box's row in the 12-column grid, or `null` when the box is not deferred at all (below 12
 * columns the read-only grid grows each row to its content, so a short placeholder would move
 * the page): then it is in view from the first render and nothing is observed.
 */
export function useInView<T extends HTMLElement>(y: number | null): {
  ref: RefObject<T | null>;
  inView: boolean;
} {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(() => {
    if (y === null || typeof IntersectionObserver === "undefined") return true;
    const viewport = typeof window === "undefined" ? null : window.innerHeight;
    return cellStartsInView(y, viewport);
  });

  useEffect(() => {
    // `y` changes with the grid's column count: a box that stops being deferred (the grid
    // reflowed below 12 columns) is left unobserved, and is observed again if it becomes
    // deferred while it has still never been in view.
    if (y === null || inView) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setInView(true);
      },
      { rootMargin: ROOT_MARGIN },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [inView, y]);

  return { ref, inView };
}
