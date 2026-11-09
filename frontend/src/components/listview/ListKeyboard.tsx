"use client";

// The key hint and the screen-reader announcement for a list table's one-key triage (SOC-15a).
// The behaviour is `useListKeyboard`; this is its only consumer, mounted by both lists.

import { useTranslations } from "next-intl";

import { useListKeyboard, type ListKeyboardOptions } from "./useListKeyboard";

/** The key caps are literal keys, not copy; the words beside them are in the catalog. */
const KEYS = [
  ["j", "next"],
  ["k", "previous"],
  ["Enter", "open"],
  ["Esc", "clear"],
] as const;

export function ListKeyboard(props: ListKeyboardOptions) {
  const t = useTranslations("ui.listKeys");
  const { note } = useListKeyboard(props);
  const say =
    note.kind === "row"
      ? t("position", { n: note.n, total: note.total, label: note.label })
      : note.kind === "last"
        ? t("last", { n: note.n, total: note.total })
        : note.kind === "cleared"
          ? t("cleared")
          : "";
  return (
    <div className="list-keys">
      <p className="list-keys-hint">
        <span className="list-keys-label">{t("label")}</span>
        {KEYS.map(([key, word]) => (
          <span key={key}>
            <kbd>{key}</kbd> {t(word)}
          </span>
        ))}
      </p>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {say}
      </p>
    </div>
  );
}
