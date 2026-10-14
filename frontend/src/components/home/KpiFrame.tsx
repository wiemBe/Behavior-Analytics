"use client";

// The frame every Home KPI card shares: title with its window, the drill-down
// link, and the states a card can be in besides "has data" (UR.4.4).
//
// The link is in the header in every state, so an analyst can always go to the
// page that owns the data, including when the card itself could not be read.
// Failure text never carries the exception: a status code at most.

import Link from "next/link";
import type { ReactNode } from "react";

import type { ResourceState } from "@/hooks/useApiResource";
import { formatClock } from "@/lib/fieldMapping";
import type { Qualifier } from "@/lib/home/qualifiers";

import "./kpi.css";

export const MISSING = "—";

export interface KpiFrameProps<T> {
  /** Stable id fragment, e.g. "total-eps". */
  id: string;
  title: string;
  href: string;
  /** The drill-down's full accessible name, e.g. "View fleet by class". */
  linkLabel: string;
  /** What the link says on screen: short, so the title row stays on one line. */
  linkText: string;
  /** Lower case, what the card reads: "fleet EPS". */
  what: string;
  /** The permission the primary read needs, quoted when it is refused. */
  permission: string;
  state: ResourceState<T>;
  /**
   * What the figures mean (window, definitions). Kept out of the card body: it is
   * the card's accessible description, so it is read out but takes no room.
   */
  description?: string;
  children: (data: T) => ReactNode;
}

export function KpiFrame<T>({
  id,
  title,
  href,
  linkLabel,
  linkText,
  what,
  permission,
  state,
  description,
  children,
}: KpiFrameProps<T>) {
  const headingId = `kpi-${id}-title`;
  const descId = `kpi-${id}-desc`;
  let body: ReactNode;
  let foot: ReactNode = null;

  if (state.status === "idle" || state.status === "loading") {
    body = (
      <p className="kpi-state" role="status" aria-live="polite">
        Loading {what}…
      </p>
    );
  } else if (state.status === "forbidden") {
    body = (
      <p className="kpi-state" role="status">
        Your role cannot read {what} (permission {permission}). Nothing was changed.
      </p>
    );
  } else if (state.status === "error") {
    body = (
      <p className="kpi-state kpi-state-error" role="alert">
        The {what} could not be loaded
        {state.httpStatus !== null ? ` (HTTP ${state.httpStatus})` : ""}. Nothing was changed.
        Use Refresh in the top bar, or check that the backend is reachable.
      </p>
    );
  } else {
    body = children(state.data);
    // The top bar says when the page refreshed; the card speaks only when its
    // own refresh failed and what it shows is older than that.
    foot = state.refreshFailed ? (
      <p className="kpi-foot kpi-refresh-failed" role="status">
        Refresh failed; showing data received at{" "}
        <time dateTime={state.receivedAt}>{formatClock(state.receivedAt)}</time>.
      </p>
    ) : null;
  }

  return (
    <article
      className="kpi"
      aria-labelledby={headingId}
      aria-describedby={description ? descId : undefined}
    >
      <header className="kpi-head">
        <h2 className="kpi-title" id={headingId}>
          {title}
        </h2>
        <Link className="kpi-link" href={href} aria-label={linkLabel}>
          {linkText}
        </Link>
      </header>
      {description ? (
        <p className="sr-only" id={descId}>
          {description}
        </p>
      ) : null}
      <div className="kpi-body">{body}</div>
      {foot}
    </article>
  );
}

/** A measured figure with its unit, or an em dash and the reason it is missing. */
export function Figure({
  value,
  unit,
  reason,
  title,
}: {
  value: string | null;
  unit: string;
  reason?: string;
  /** The definition, as a tooltip. */
  title?: string;
}) {
  if (value === null) {
    return (
      <p className="kpi-figure" title={title}>
        <span className="kpi-value num">
          {MISSING}
        </span>
        {reason ? <span className="kpi-reason">{reason}</span> : null}
      </p>
    );
  }
  return (
    <p className="kpi-figure" title={title}>
      <span className="kpi-value num">{value}</span> <span className="kpi-unit">{unit}</span>
    </p>
  );
}

const QUALIFIER_WORDS: Record<Qualifier["kind"], { pill: string; tone: string }> = {
  INSUFFICIENT_DATA: { pill: "Insufficient data", tone: "" },
  PARTIAL: { pill: "Partial", tone: "warn" },
  TRUNCATED: { pill: "Truncated", tone: "warn" },
  NOT_COLLECTED: { pill: "Not collected", tone: "" },
};

function qualifierText(q: Qualifier): string {
  switch (q.kind) {
    case "PARTIAL":
      // "720 of 864 intervals": the pill already says it is partial.
      return `${q.have.toLocaleString("en-US")} of ${q.of.toLocaleString("en-US")} ${q.unit}`;
    case "INSUFFICIENT_DATA":
      return q.reason;
    case "NOT_COLLECTED":
      // The pill says "Not collected"; the line gives why.
      return q.reason;
    default:
      return q.text;
  }
}

/** The honesty qualifiers on one line: the pill in words, then its short count. */
export function Qualifiers({ items, extra }: { items: Qualifier[]; extra?: string }) {
  if (items.length === 0 && !extra) return null;
  return (
    <p className="kpi-qualifiers">
      {items.map((q) => (
        <span className="kpi-qualifier" key={q.kind}>
          <span className={`pill ${QUALIFIER_WORDS[q.kind].tone}`.trim()}>
            {QUALIFIER_WORDS[q.kind].pill}
          </span>
          <span className="kpi-qualifier-text">{qualifierText(q)}</span>
        </span>
      ))}
      {extra ? <span className="kpi-qualifier-text">{extra}</span> : null}
    </p>
  );
}

/** Parts of one line, set off by a middle dot. Empty parts are dropped. */
export function Line({
  parts,
  title,
}: {
  parts: (string | null | false | undefined)[];
  title?: string;
}) {
  const shown = parts.filter((p): p is string => !!p);
  if (shown.length === 0) return null;
  return (
    <p className="kpi-line num" title={title}>
      {shown.join(" \u00b7 ")}
    </p>
  );
}

/** "1,234.56": EPS with thousands separators and two decimals. */
export function n(value: number): string {
  return value.toLocaleString("en-US");
}
