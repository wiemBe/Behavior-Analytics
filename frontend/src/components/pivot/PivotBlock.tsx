"use client";

// "Investigate in QRadar": the AQL, the UTC window and a console link for one object.
//
// The text is built by the backend from stored, validated values and is shown for the analyst
// to copy or open in the console. The product never runs it, and nothing on this page
// assembles or edits AQL. It is rendered as text, never as markup.

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import type { Pivot } from "@/lib/pivot";

import "./PivotBlock.css";

type Copied = "aql" | "window" | "denied" | null;

export function PivotBlock({ pivot, failed = false }: { pivot: Pivot | null; failed?: boolean }) {
  const t = useTranslations("ui.pivot");
  const [copied, setCopied] = useState<Copied>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  if (failed || pivot === null) {
    return (
      <section className="pivot" aria-labelledby="pivot-title">
        <h2 className="section-title" id="pivot-title">
          {t("title")}
        </h2>
        <div className="notice" role="alert">
          <p>{t("failed")}</p>
        </div>
      </section>
    );
  }

  const windowText = t("rangeText", {
    start: utcTimeText(pivot.time_range.start, "full"),
    stop: utcTimeText(pivot.time_range.stop, "full"),
  });

  const copy = async (text: string, what: Exclude<Copied, "denied" | null>) => {
    let next: Copied = what;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // A denied clipboard is not a dialog; the text is selectable on the page.
      next = "denied";
    }
    setCopied(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 3000);
  };

  const showEvents = pivot.aql_search_url !== null && pivot.aql_search_url !== pivot.console_url;
  const notes = pivot.notes.filter((note) => note !== pivot.console_unavailable_reason);

  return (
    <section className="pivot" aria-labelledby="pivot-title">
      <h2 className="section-title" id="pivot-title">
        {t("title")}
      </h2>
      <p className="subtitle">{t("intro")}</p>
      <pre className="pivot-aql" aria-label={t("queryLabel")}>
        <code>{pivot.aql}</code>
      </pre>
      <dl className="pivot-range">
        <dt>{t("rangeLabel")}</dt>
        <dd>
          <UtcTime value={pivot.time_range.start} /> {t("rangeTo")}{" "}
          <UtcTime value={pivot.time_range.stop} />
        </dd>
      </dl>
      <div className="pivot-actions">
        <button type="button" className="action secondary" onClick={() => copy(pivot.aql, "aql")}>
          {t("copyAql")}
        </button>
        {pivot.console_url !== null && (
          <a
            className="action secondary"
            href={pivot.console_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("open")} <span className="sr-only">{t("newTab")}</span>
          </a>
        )}
        {showEvents && pivot.aql_search_url !== null && (
          <a
            className="action secondary"
            href={pivot.aql_search_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("openEvents")} <span className="sr-only">{t("newTab")}</span>
          </a>
        )}
        <button
          type="button"
          className="action secondary"
          onClick={() => copy(windowText, "window")}
        >
          {t("copyWindow")}
        </button>
        {/* Announced when it changes, so a copy is confirmed to a screen reader too. */}
        <span role="status" aria-live="polite" className="muted pivot-status">
          {copied === "aql" && t("aqlCopied")}
          {copied === "window" && t("windowCopied")}
          {copied === "denied" && t("copyFailed")}
        </span>
      </div>
      {pivot.console_url === null && pivot.console_unavailable_reason && (
        <p className="subtitle pivot-why">
          {t("noConsole", { reason: pivot.console_unavailable_reason })}
        </p>
      )}
      {notes.length > 0 && (
        <div className="pivot-notes">
          <h3 className="pivot-notes-title">{t("notes")}</h3>
          <ul>
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
