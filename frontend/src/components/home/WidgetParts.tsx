"use client";

// The frame and the states the four Home tables share (UR.4.1, UR.4.4).
//
// A widget is one panel: a title that says what it shows and for what window, a
// drill-down, and a body. Every state is plain text, as `LoadingState` is: no
// spinner, no skeleton, and no second bordered box inside the panel.
//
// None of these renders an exception's text. A failed read says what failed and
// what to do; a refused one names the permission.

import Link from "next/link";
import type { ReactNode } from "react";

import { formatClockUtc } from "@/components/shell/AutoRefresh";
import type { ResourceState } from "@/hooks/useApiResource";
import { NOT_MEASURED } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { formatAgo } from "@/lib/home/format";
import type { Qualifier } from "@/lib/home/qualifiers";

import "./tables.css";

export function Widget({
  id,
  title,
  windowText,
  badge,
  viewAll,
  children,
}: {
  /** Unique on the page: names the section. */
  id: string;
  title: string;
  /** The window the figures cover, e.g. "24 h". */
  windowText?: string;
  badge?: string;
  viewAll: { href: string; label: string };
  children: ReactNode;
}) {
  return (
    <section className="tw" aria-labelledby={`${id}-title`}>
      <header className="tw-head">
        <h2 className="tw-title" id={`${id}-title`}>
          {title}
          {windowText && <span className="tw-window">{windowText}</span>}
        </h2>
        {badge && <span className="pill">{badge}</span>}
        <Link className="tw-all" href={viewAll.href}>
          {viewAll.label}
        </Link>
      </header>
      <div className="tw-body">{children}</div>
    </section>
  );
}

export function WidgetLoading({ what }: { what: string }) {
  return (
    <p className="tw-state" role="status" aria-live="polite">
      Loading {what}…
    </p>
  );
}

/** A read that failed: what failed and what to do. Never the exception text. */
export function WidgetError({ what, httpStatus }: { what: string; httpStatus: number | null }) {
  return (
    <div className="tw-state" role="alert">
      <p className="tw-state">
        <strong>{what.charAt(0).toUpperCase() + what.slice(1)} could not be read.</strong>
      </p>
      <p className="tw-state">
        {httpStatus === null
          ? "The backend did not answer. "
          : `The backend answered with HTTP ${httpStatus}. `}
        Nothing was changed. The figures return on the next refresh; if they do not, check that
        the backend is reachable.
      </p>
    </div>
  );
}

export function WidgetForbidden({ what, permission }: { what: string; permission: string }) {
  return (
    <p className="tw-state" role="status">
      Your role cannot read {what} (permission {permission}). Nothing was changed.
    </p>
  );
}

/** A refresh that failed while the previous answer stays on screen. */
export function RefreshNote({
  failed,
  receivedAt,
}: {
  failed: { httpStatus: number | null } | null;
  receivedAt: string;
}) {
  if (!failed) return null;
  return (
    <p className="tw-note warn" role="status">
      The latest refresh failed
      {failed.httpStatus === null ? "" : ` (HTTP ${failed.httpStatus})`}. Showing the data
      received at {formatClockUtc(receivedAt)}.
    </p>
  );
}

const QUALIFIER_WORD: Record<Qualifier["kind"], string> = {
  INSUFFICIENT_DATA: "Insufficient data",
  PARTIAL: "Partial",
  TRUNCATED: "Truncated",
  NOT_COLLECTED: "Not collected",
};

function qualifierText(q: Qualifier): string {
  return q.kind === "INSUFFICIENT_DATA" || q.kind === "NOT_COLLECTED" ? q.reason : q.text;
}

/** The caveats on data that is shown. Each states its kind in words. */
export function QualifierList({ qualifiers }: { qualifiers: Qualifier[] }) {
  if (qualifiers.length === 0) return null;
  return (
    <ul className="tw-qual">
      {qualifiers.map((q, i) => (
        <li key={`${q.kind}-${i}`} className={q.kind === "PARTIAL" || q.kind === "TRUNCATED" ? "warn" : ""}>
          <span className="kind">{QUALIFIER_WORD[q.kind]}</span>: {qualifierText(q)}
        </li>
      ))}
    </ul>
  );
}

/** An unmeasured figure: the dash, with the reason for assistive technology. */
export function Unmeasured({ reason }: { reason?: string | null }) {
  return (
    <span className="muted" title={reason ?? undefined}>
      {NOT_MEASURED}
      {reason && <span className="sr-only"> {reason}</span>}
    </span>
  );
}

/**
 * An instant in UTC with its zone, as a `<time>`, and its age in muted text.
 * Null prints a dash, not a date.
 */
export function When({ iso }: { iso: string | null }) {
  if (!iso) return <Unmeasured reason="No time recorded" />;
  const ago = formatAgo(iso, Date.now());
  return (
    <span className="tw-when">
      <time dateTime={iso} title={formatDateTime(iso)}>
        {formatDateTimeCompact(iso)}
      </time>
      {ago && <span className="tw-sub">{ago}</span>}
    </span>
  );
}

/** What a widget needs from its primary read, once it has settled `ok`. */
export interface Settled<T> {
  data: T;
  receivedAt: string;
  refreshFailed: { httpStatus: number | null } | null;
}

/**
 * Renders the primary read's state: loading, refused, failed, or the body with
 * what the read returned. A secondary read is handled by the widget itself.
 */
export function Gate<T>({
  state,
  what,
  permission,
  children,
}: {
  state: ResourceState<T>;
  what: string;
  permission: string;
  children: (ok: Settled<T>) => ReactNode;
}) {
  if (state.status === "idle" || state.status === "loading") return <WidgetLoading what={what} />;
  if (state.status === "forbidden") return <WidgetForbidden what={what} permission={permission} />;
  if (state.status === "error") return <WidgetError what={what} httpStatus={state.httpStatus} />;
  return (
    <>
      {children({ data: state.data, receivedAt: state.receivedAt, refreshFailed: state.refreshFailed })}
      <RefreshNote failed={state.refreshFailed} receivedAt={state.receivedAt} />
    </>
  );
}

/** A secondary read's data, or null with the reason it is not there. */
export function secondary<T>(state: ResourceState<T>): { data: T | null; gap: string | null; pending: boolean } {
  switch (state.status) {
    case "ok":
      return { data: state.data, gap: null, pending: false };
    case "forbidden":
      return { data: null, gap: "your role cannot read it", pending: false };
    case "error":
      return { data: null, gap: "it could not be read", pending: false };
    default:
      return { data: null, gap: null, pending: true };
  }
}
