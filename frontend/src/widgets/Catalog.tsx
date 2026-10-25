"use client";

// The catalog drawer (UR.5.1, UR.5.6): the widgets that exist, by category, each with what it
// reads and the permission it needs. A `blocked` widget is listed disabled with its reason; a
// widget the caller's role cannot read is disabled with the permission named, once `GET /me`
// says what the role holds. Without `/me` the catalog lists everything and a placed widget's
// own forbidden state is the guard. It lists only what exists: it does not tease.
//
// A real dialog: `showModal` makes the page behind it inert and gives `Esc`; focus moves to the
// Close button on open and returns to the trigger on close.

import { useTranslations } from "next-intl";
import { useEffect, useRef, type RefObject } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { englishText } from "@/widgets/englishText";
import { homeReads } from "@/lib/api";

import { useWidgetCopy } from "./copy";
import { WIDGETS } from "./registry";
import type { AnyWidgetDef, WidgetCategory } from "./types";

const CATEGORIES: WidgetCategory[] = ["KPI", "Chart", "Table", "Health", "Admin"];

/** The neutral reason, in English: information, not a warning. */
export const ALREADY_PLACED = "Already on this dashboard.";

/** Widgets one dashboard may hold (UR.5.5). */
export const MAX_WIDGETS = 24;

export type Unavailable = { kind: "blocked" | "role" | "placed" | "full"; text: string };

/**
 * Why a catalog entry cannot be added, with the kind that decides how it is drawn, or null when
 * it can be added. `t` is the root translator; the default keeps the English sentences.
 */
export function unavailable(
  def: AnyWidgetDef,
  held: ReadonlySet<string> | null,
  placedTypes: ReadonlySet<string>,
  placedCount: number,
  t: TextTranslator = englishText,
): Unavailable | null {
  if (def.status === "blocked") {
    const key = `widgets.registry.${def.type.replace(/\./g, "_")}.blockedBy`;
    const reason = t.has?.(key) ? t(key) : (def.blockedBy ?? "");
    return { kind: "blocked", text: t("widgets.notAvailable", { reason }) };
  }
  if (held) {
    const missing = def.requires.filter((p) => !held.has(p));
    if (missing.length > 0) {
      return {
        kind: "role",
        text: t("widgets.catalog.roleLacks", { count: missing.length, permissions: missing.join(", ") }),
      };
    }
  }
  // A widget without config keys shows the same thing twice, so a second copy is refused. One
  // with config keys (an endpoint, a source) can be placed once per subject, so it is not.
  if (placedTypes.has(def.type) && Object.keys(def.config).length === 0) {
    return { kind: "placed", text: t("widgets.catalog.alreadyPlaced") };
  }
  if (placedCount >= MAX_WIDGETS) {
    return { kind: "full", text: t("widgets.catalog.full", { max: MAX_WIDGETS }) };
  }
  return null;
}

/** Why a catalog entry cannot be added, or null when it can. */
export function unavailableReason(
  def: AnyWidgetDef,
  held: ReadonlySet<string> | null,
  placedTypes: ReadonlySet<string>,
  placedCount: number,
  t: TextTranslator = englishText,
): string | null {
  return unavailable(def, held, placedTypes, placedCount, t)?.text ?? null;
}

/** A route as the analyst reads it: a path parameter is `{id}`, not a template placeholder. */
function showRoute(route: string): string {
  return route.replace(/\$\{(\w+)\}/g, "{$1}");
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
  const t = useTranslations("widgets.catalog");
  const root = asTextTranslator(useTranslations());
  const copy = useWidgetCopy();
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
          {t("title")}
        </h2>
        <button type="button" ref={closeRef} className="action secondary" onClick={onClose}>
          {t("close")}
        </button>
      </header>
      {caller.status === "ok" ? null : (
        <p className="dg-catalog-note">
          {caller.status === "idle" || caller.status === "loading"
            ? t("checking")
            : t("permissionsUnread")}
        </p>
      )}
      <div className="dg-catalog-body">
        {CATEGORIES.map((category) => {
          const defs = WIDGETS.filter((w) => w.category === category);
          if (defs.length === 0) return null;
          return (
            <section key={category} aria-labelledby={`dg-cat-${category}`}>
              <h3 id={`dg-cat-${category}`} className="dg-catalog-category">
                {copy.category(category)}
              </h3>
              <ul className="dg-catalog-list">
                {defs.map((def) => {
                  const why = unavailable(def, held, placedTypes, placedCount, root);
                  const reason = why?.text ?? null;
                  const reasonId = `dg-reason-${def.type}`;
                  return (
                    <li key={def.type} className="dg-catalog-item">
                      <div className="dg-catalog-text">
                        <p className="dg-catalog-name">{copy.title(def)}</p>
                        <p className="dg-catalog-desc">{copy.description(def)}</p>
                        {def.status === "live" ? (
                          <dl className="dg-catalog-facts">
                            <div>
                              <dt>{t("reads")}</dt>
                              <dd>{def.sources.map(showRoute).join(", ")}</dd>
                            </div>
                            <div>
                              <dt>{t("needs")}</dt>
                              <dd>{def.requires.join(", ")}</dd>
                            </div>
                          </dl>
                        ) : null}
                        {reason ? (
                          <p
                            className={`dg-catalog-reason${why?.kind === "placed" ? " info" : ""}`}
                            id={reasonId}
                          >
                            {reason}
                          </p>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        className="action secondary"
                        disabled={reason !== null}
                        aria-describedby={reason ? reasonId : undefined}
                        aria-label={t("addNamed", { title: copy.title(def) })}
                        onClick={() => onAdd(def)}
                      >
                        {t("add")}
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
