"use client";

// One-key triage on a list table (SOC-15a): j / k move a marked row down / up, Enter or o opens
// the marked row's detail, Escape clears the mark.
//
// The table is server-rendered, so the hook works on the DOM: the rows are the table's body rows
// except a disclosure's detail row, and the mark (`data-kbd-row`, `aria-current`) lives on the
// row itself. The mark is the single source of truth for the position, so a row set that was
// replaced by a refresh never leaves a stale index behind.
//
// Keys are left alone while focus is in a form control or an editable region, while Ctrl / Alt /
// Meta is held (Ctrl K stays the search), while a dialog is open, and, for Enter and o, while
// focus is on a link, button or summary, which have their own meaning for them.

import { useCallback, useEffect, useState } from "react";

export const KBD_ROW_ATTR = "data-kbd-row";

export interface ListKeyboardOptions {
  /** CSS selector of the table the keys act on. */
  table: string;
  /** CSS selector, inside a row, of the link Enter / o follows. */
  open: string;
}

export type ListKeyboardNote =
  | { kind: "none" }
  | { kind: "row"; n: number; total: number; label: string }
  | { kind: "last"; n: number; total: number; label: string }
  | { kind: "cleared" };

const LABEL_MAX = 80;

function typingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  return el.closest("input, select, textarea, [contenteditable=''], [contenteditable='true']") !== null;
}

function ownsEnter(el: EventTarget | null): boolean {
  return el instanceof HTMLElement && el.closest("a, button, summary") !== null;
}

function rowsOf(table: HTMLElement): HTMLTableRowElement[] {
  return Array.from(table.querySelectorAll<HTMLTableRowElement>("tbody > tr")).filter(
    (r) => !r.classList.contains("row-detail"),
  );
}

function labelOf(row: HTMLElement): string {
  const text = (row.querySelector("td")?.textContent ?? "").replace(/\s+/g, " ").trim();
  return text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text;
}

function dialogOpen(): boolean {
  return document.querySelector('dialog[open], [aria-modal="true"]') !== null;
}

export function useListKeyboard({ table: selector, open }: ListKeyboardOptions): {
  note: ListKeyboardNote;
} {
  const [note, setNote] = useState<ListKeyboardNote>({ kind: "none" });

  const clear = useCallback((table: HTMLElement) => {
    table.querySelectorAll(`[${KBD_ROW_ATTR}]`).forEach((r) => {
      r.removeAttribute(KBD_ROW_ATTR);
      r.removeAttribute("aria-current");
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey) return;
      if (typingTarget(e.target) || dialogOpen()) return;
      const key = e.key;
      if (!["j", "k", "Enter", "o", "Escape"].includes(key)) return;
      const table = document.querySelector<HTMLElement>(selector);
      if (!table) return;
      const rows = rowsOf(table);
      if (rows.length === 0) return;
      const at = rows.findIndex((r) => r.hasAttribute(KBD_ROW_ATTR));

      if (key === "Escape") {
        if (at < 0) return;
        clear(table);
        setNote({ kind: "cleared" });
        return;
      }
      if (key === "Enter" || key === "o") {
        if (at < 0 || ownsEnter(e.target)) return;
        const link = rows[at].querySelector<HTMLElement>(open);
        if (!link) return;
        e.preventDefault();
        link.click();
        return;
      }
      // j / k. `k` with no mark does nothing, so the first `j` is always the first row.
      if (key === "k" && at < 0) return;
      const next = key === "j" ? at + 1 : Math.max(at - 1, 0);
      e.preventDefault();
      if (next >= rows.length) {
        // The end of the page: no wrap and no silent paging.
        setNote({ kind: "last", n: at + 1, total: rows.length, label: labelOf(rows[at]) });
        return;
      }
      if (next === at) return;
      clear(table);
      const row = rows[next];
      row.setAttribute(KBD_ROW_ATTR, "true");
      row.setAttribute("aria-current", "true");
      row.scrollIntoView?.({ block: "nearest" });
      setNote({ kind: "row", n: next + 1, total: rows.length, label: labelOf(row) });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selector, open, clear]);

  return { note };
}
