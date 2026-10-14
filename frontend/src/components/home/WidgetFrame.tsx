"use client";

// The one frame every Home widget shares (UR.4.1, UR.4.4, UR.5.4): a title that says what
// it shows and for what window, an optional slot for controls beside the title, the
// drill-down link, and the states a widget can be in besides "has data".
//
// The link is in the header in every state, so an analyst can always go to the page that
// owns the data, including when the widget itself could not be read. Every state is plain
// text: no spinner, no skeleton and no second bordered box inside the panel. None of them
// renders an exception's text; a failed read says what failed and what to do, a refused
// one names the permission, and a failed refresh keeps the previous answer on screen.
//
// Two variants share the states and differ in density and voice: `kpi` is the compact card
// of the KPI row (one terse paragraph per state), `panel` is the larger widget.

import Link from "next/link";
import type { ReactNode } from "react";

import { formatClockUtc } from "@/components/shell/AutoRefresh";
import type { ResourceState } from "@/hooks/useApiResource";
import { formatClock } from "@/lib/fieldMapping";
import type { Qualifier } from "@/lib/home/qualifiers";

import "./kpi.css";
import "./tables.css";

export type WidgetVariant = "kpi" | "panel";

/** What a widget gets from its primary read once it has settled `ok`. */
export interface Settled<T> {
  data: T;
  receivedAt: string;
  refreshFailed: { httpStatus: number | null } | null;
}

export interface WidgetFrameProps<T> {
  /** Unique on the page: names the section. */
  id: string;
  title: string;
  /** The window the figures cover, e.g. "24 h". */
  windowText?: string;
  /** One pill in the title row: the widget's qualifier. */
  badge?: string;
  /** Controls beside the title, e.g. the window presets. */
  controls?: ReactNode;
  link: {
    href: string;
    /** What the link says on screen. */
    label: string;
    /** The full accessible name, when the short label is not enough. */
    ariaLabel?: string;
  };
  /** Lower case, what the widget reads: "fleet EPS". */
  what: string;
  /** The permission the primary read needs, quoted when it is refused. */
  permission: string;
  state: ResourceState<T>;
  /**
   * What the figures mean (window, definitions). Kept out of the body: it is the widget's
   * accessible description, so it is read out but takes no room.
   */
  description?: string;
  variant?: WidgetVariant;
  /** Let a chart consume the panel body height left by its grid row. */
  fillBody?: boolean;
  children: (ok: Settled<T>) => ReactNode;
}

export function WidgetLoading({ what, variant = "panel" }: { what: string; variant?: WidgetVariant }) {
  return (
    <p className={variant === "kpi" ? "kpi-state" : "tw-state"} role="status" aria-live="polite">
      Loading {what}…
    </p>
  );
}

const QUALIFIER_WORDS: Record<Qualifier["kind"], { pill: string; tone: string }> = {
  INSUFFICIENT_DATA: { pill: "Insufficient data", tone: "" },
  PARTIAL: { pill: "Partial", tone: "warn" },
  TRUNCATED: { pill: "Truncated", tone: "warn" },
  NOT_COLLECTED: { pill: "Not collected", tone: "" },
};

function qualifierText(q: Qualifier, compact: boolean): string {
  if (q.kind === "PARTIAL" && compact) {
    return `${q.have.toLocaleString("en-US")} of ${q.of.toLocaleString("en-US")} ${q.unit}`;
  }
  return q.kind === "INSUFFICIENT_DATA" || q.kind === "NOT_COLLECTED" ? q.reason : q.text;
}

/** Honesty qualifiers, with the established compact KPI or panel presentation. */
export function WidgetQualifiers({
  qualifiers,
  variant = "panel",
  extra,
}: {
  qualifiers: Qualifier[];
  variant?: WidgetVariant;
  extra?: string;
}) {
  if (qualifiers.length === 0 && !extra) return null;
  if (variant === "kpi") {
    return (
      <p className="kpi-qualifiers">
        {qualifiers.map((q) => (
          <span className="kpi-qualifier" key={q.kind}>
            <span className={`pill ${QUALIFIER_WORDS[q.kind].tone}`.trim()}>
              {QUALIFIER_WORDS[q.kind].pill}
            </span>
            <span className="kpi-qualifier-text">{qualifierText(q, true)}</span>
          </span>
        ))}
        {extra ? <span className="kpi-qualifier-text">{extra}</span> : null}
      </p>
    );
  }
  return (
    <ul className="tw-qual">
      {qualifiers.map((q, i) => (
        <li
          key={`${q.kind}-${i}`}
          className={q.kind === "PARTIAL" || q.kind === "TRUNCATED" ? "warn" : ""}
        >
          <span className="kind">{QUALIFIER_WORDS[q.kind].pill}</span>: {qualifierText(q, false)}
        </li>
      ))}
    </ul>
  );
}

/** A read that failed: what failed and what to do. Never the exception text. */
function FailedState({
  what,
  httpStatus,
  variant,
}: {
  what: string;
  httpStatus: number | null;
  variant: WidgetVariant;
}) {
  const subject = what.replace(/^the\s+/i, "");
  return (
    <div className={`${variant === "kpi" ? "kpi-state" : "tw-state"} widget-error`} role="alert">
      <p className="widget-error-title">
        <strong>The {subject} could not be read.</strong>
      </p>
      <p className="widget-error-action">
        {httpStatus === null
          ? "The backend did not answer. "
          : `The backend answered with HTTP ${httpStatus}. `}
        Nothing was changed. Use Refresh in the top bar; if it still fails, check that the backend
        is reachable.
      </p>
    </div>
  );
}

function ForbiddenState({
  what,
  permission,
  variant,
}: {
  what: string;
  permission: string;
  variant: WidgetVariant;
}) {
  return (
    <p className={variant === "kpi" ? "kpi-state" : "tw-state"} role="status">
      Your role cannot read {what} (permission {permission}). Nothing was changed.
    </p>
  );
}

/** A refresh that failed while the previous answer stays on screen. */
function RefreshNote({
  failed,
  receivedAt,
  variant,
}: {
  failed: { httpStatus: number | null } | null;
  receivedAt: string;
  variant: WidgetVariant;
}) {
  if (!failed) return null;
  if (variant === "kpi") {
    return (
      <p className="kpi-foot kpi-refresh-failed" role="status">
        Refresh failed; showing data received at{" "}
        <time dateTime={receivedAt}>{formatClock(receivedAt)}</time>.
      </p>
    );
  }
  return (
    <p className="tw-note warn" role="status">
      The latest refresh failed
      {failed.httpStatus === null ? "" : ` (HTTP ${failed.httpStatus})`}. Showing the data
      received at {formatClockUtc(receivedAt)}.
    </p>
  );
}

export function WidgetFrame<T>({
  id,
  title,
  windowText,
  badge,
  controls,
  link,
  what,
  permission,
  state,
  description,
  variant = "panel",
  fillBody = false,
  children,
}: WidgetFrameProps<T>) {
  const kpi = variant === "kpi";
  const headingId = `${id}-title`;
  const descId = `${id}-desc`;
  let body: ReactNode;

  if (state.status === "idle" || state.status === "loading") {
    body = <WidgetLoading what={what} variant={variant} />;
  } else if (state.status === "forbidden") {
    body = <ForbiddenState what={what} permission={permission} variant={variant} />;
  } else if (state.status === "error") {
    body = <FailedState what={what} httpStatus={state.httpStatus} variant={variant} />;
  } else {
    body = (
      <>
        {children({
          data: state.data,
          receivedAt: state.receivedAt,
          refreshFailed: state.refreshFailed,
        })}
        <RefreshNote failed={state.refreshFailed} receivedAt={state.receivedAt} variant={variant} />
      </>
    );
  }

  const Root = kpi ? "article" : "section";
  return (
    <Root
      className={kpi ? "kpi" : "tw"}
      aria-labelledby={headingId}
      aria-describedby={description ? descId : undefined}
    >
      <header className={kpi ? "kpi-head" : "tw-head"}>
        <h2 className={kpi ? "kpi-title" : "tw-title"} id={headingId}>
          {title}
          {windowText && <span className="tw-window">{windowText}</span>}
        </h2>
        {badge && <span className="pill">{badge}</span>}
        {controls}
        <Link
          className={kpi ? "kpi-link" : "tw-all"}
          href={link.href}
          aria-label={link.ariaLabel}
        >
          {link.label}
        </Link>
      </header>
      {description ? (
        <p className="sr-only" id={descId}>
          {description}
        </p>
      ) : null}
      <div className={kpi ? "kpi-body" : `tw-body${fillBody ? " tw-body-fill" : ""}`}>
        {body}
      </div>
    </Root>
  );
}

/** A secondary read's data, or null with the reason it is not there. */
export function secondary<T>(state: ResourceState<T>): {
  data: T | null;
  gap: string | null;
  pending: boolean;
} {
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
