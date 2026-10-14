"use client";

// The catalog drawer (UR.5.1, UR.5.6): the widgets that exist, by category, each with what it
// reads and the permission it needs. A `blocked` widget is listed disabled with its reason; a
// widget the caller's role cannot read is disabled with the permission named, once `GET /me`
// says what the role holds. Without `/me` the catalog lists everything and a placed widget's
// own forbidden state is the guard. It lists only what exists: it does not tease.
//
// A real dialog: `showModal` makes the page behind it inert and gives `Esc`; focus moves to the
// Close button on open and returns to the trigger on close.

import { useEffect, useRef, type RefObject } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";

import { WIDGETS } from "./registry";
import type { AnyWidgetDef, WidgetCategory } from "./types";

const CATEGORIES: WidgetCategory[] = ["KPI", "Chart", "Table", "Health", "Admin"];

/** Widgets one dashboard may hold (UR.5.5). */
export const MAX_WIDGETS = 24;

/** Why a catalog entry cannot be added, or null when it can. */
export function unavailableReason(
  def: AnyWidgetDef,
  held: ReadonlySet<string> | null,
  placedTypes: ReadonlySet<string>,
  placedCount: number,
): string | null {
  if (def.status === "blocked") return `Not available: ${def.blockedBy}.`;
  if (held) {
    const missing = def.requires.filter((p) => !held.has(p));
    if (missing.length > 0) {
      return `Your role lacks ${missing.length === 1 ? "permission" : "permissions"} ${missing.join(", ")}.`;
    }
  }
  // None of today's widgets has configuration, so a second copy would be identical. UI-3.3's
  // configuration makes duplicates meaningful; this guard then goes.
  if (placedTypes.has(def.type)) return "Already on this dashboard.";
  if (placedCount >= MAX_WIDGETS) {
    return `A dashboard holds at most ${MAX_WIDGETS} widgets.`;
  }
  return null;
}

export function Catalog({
  placedTypes,
  placedCount,
  onAdd,
  onClose,
}: {
  placedTypes: ReadonlySet<string>;
  placedCount: number;
  onAdd: (def: AnyWidgetDef) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef: RefObject<HTMLButtonElement | null> = useRef(null);
  const me = homeReads.me();
  const caller = useApiResource(me.key, me.fetch);
  const held = caller.status === "ok" ? new Set(caller.data.effective_permissions) : null;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="dg-catalog"
      aria-labelledby="dg-catalog-title"
      onCancel={(event) => {
        // Esc: the dialog closes by unmounting, so the page owns the focus return.
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="dg-catalog-head">
        <h2 id="dg-catalog-title" className="dg-catalog-title">
          Add a widget
        </h2>
        <button type="button" ref={closeRef} className="action secondary" onClick={onClose}>
          Close
        </button>
      </header>
      {caller.status === "ok" ? null : (
        <p className="dg-catalog-note">
          {caller.status === "idle" || caller.status === "loading"
            ? "Checking what your role can read…"
            : "Your permissions could not be read, so every widget is listed. A widget your role cannot read shows that on the dashboard."}
        </p>
      )}
      <div className="dg-catalog-body">
        {CATEGORIES.map((category) => {
          const defs = WIDGETS.filter((w) => w.category === category);
          if (defs.length === 0) return null;
          return (
            <section key={category} aria-labelledby={`dg-cat-${category}`}>
              <h3 id={`dg-cat-${category}`} className="dg-catalog-category">
                {category}
              </h3>
              <ul className="dg-catalog-list">
                {defs.map((def) => {
                  const reason = unavailableReason(def, held, placedTypes, placedCount);
                  const reasonId = `dg-reason-${def.type}`;
                  return (
                    <li key={def.type} className="dg-catalog-item">
                      <div className="dg-catalog-text">
                        <p className="dg-catalog-name">{def.title}</p>
                        <p className="dg-catalog-desc">{def.description}</p>
                        {def.status === "live" ? (
                          <dl className="dg-catalog-facts">
                            <div>
                              <dt>Reads</dt>
                              <dd>{def.sources.join(", ")}</dd>
                            </div>
                            <div>
                              <dt>Needs</dt>
                              <dd>{def.requires.join(", ")}</dd>
                            </div>
                          </dl>
                        ) : null}
                        {reason ? (
                          <p className="dg-catalog-reason" id={reasonId}>
                            {reason}
                          </p>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        className="action secondary"
                        disabled={reason !== null}
                        aria-describedby={reason ? reasonId : undefined}
                        aria-label={`Add ${def.title}`}
                        onClick={() => onAdd(def)}
                      >
                        Add
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>
    </dialog>
  );
}
