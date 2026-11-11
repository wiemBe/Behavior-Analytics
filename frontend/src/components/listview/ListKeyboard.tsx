"use client";

// The key hint and the screen-reader announcement for a list table's one-key triage (SOC-15a).
// The behaviour is `useListKeyboard`; this is its only consumer, mounted by both lists.

import { useTranslations } from "next-intl";

import { useListKeyboard, type ListKeyboardOptions } from "./useListKeyboard";

/** `g` then a letter (SOC-15b): the sequence itself lives in the shell (`GoKeys`). */
const GO = [
  ["d", "dashboard"],
  ["q", "queue"],
  ["a", "anomalies"],
  ["l", "alerts"],
  ["o", "offenses"],
] as const;

const GO_PREFIX = "g";

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
  const extra: (readonly ["a" | "n", "acknowledge" | "note"])[] = [];
  if (props.acknowledge) extra.push(["a", "acknowledge"]);
  if (props.note) extra.push(["n", "note"]);
  const result =
    note.kind === "acked"
      ? { tone: "ok", text: t("acknowledged", { title: note.title }) }
      : note.kind === "ackFailed"
        ? { tone: "crit", text: note.message }
        : note.kind === "notOpen"
          ? { tone: "", text: t("notOpen") }
          : null;
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
        {[...KEYS, ...extra].map(([key, word]) => (
          <span key={key}>
            <kbd>{key}</kbd> {t(word)}
          </span>
        ))}
      </p>
      <p className="list-keys-hint list-keys-go">
        <span className="list-keys-label">{t("goLabel")}</span>
        {GO.map(([key, word]) => (
          <span key={key}>
            <kbd>{GO_PREFIX}</kbd> <kbd>{key}</kbd> {t(`go.${word}`)}
          </span>
        ))}
      </p>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {say}
      </p>
      <p className={result ? `list-keys-result pill ${result.tone}` : "list-keys-result"} aria-live="polite" aria-atomic="true">
        {result?.text ?? ""}
      </p>
    </div>
  );
}
