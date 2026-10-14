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
// The states are the union of UR.5.4: `loading`, `ok`, `empty`, `no_data`, `not_collected`,
// `stale`, `error` and `forbidden`. A widget that reads through `useApiResource` passes the
// hook's `ResourceState` and gets the first, `ok`, `error` and `forbidden` of them; a widget
// that knows more (a stale answer, an honest empty) passes a `WidgetState`. The qualifiers
// (`INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED`) are attached to `ok` by the widget's body.
//
// Two variants share the states and differ in density and voice: `kpi` is the compact card
// of the KPI row (one terse paragraph per state), `panel` is the larger widget.

import Link from "next/link";
import { createContext, useContext, type ReactNode } from "react";

import { formatClockUtc } from "@/components/shell/AutoRefresh";
import type { ResourceState } from "@/hooks/useApiResource";
import { formatClock } from "@/lib/fieldMapping";
import type { Qualifier } from "@/lib/home/qualifiers";
import { RangeControl } from "@/widgets/RangeControl";
import { effectiveRange, WidgetRuntimeContext } from "@/widgets/runtime";
import type { WidgetState } from "@/widgets/types";

import "./kpi.css";
import "./tables.css";

export type WidgetVariant = "kpi" | "panel";

/**
 * Set by a dashboard in customize mode around each placed widget. The frame then offers
 * Remove in place of the drill-down link, so a widget keeps its geometry while it is edited.
 */
export const WidgetEditContext = createContext<{
  onRemove: () => void;
  arrange?: ReactNode;
} | null>(null);

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
  state: ResourceState<T> | WidgetState<T>;
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
  action,
  variant,
}: {
  what: string;
  httpStatus: number | null;
  /** What the analyst can do, when the widget says it; else the standard refresh advice. */
  action?: string;
  variant: WidgetVariant;
}) {
  const subject = what.replace(/^the\s+/i, "");
  return (
    <div className={`${variant === "kpi" ? "kpi-state" : "tw-state"} widget-error`} role="alert">
      <p className="widget-error-title">
        <strong>The {subject} could not be read.</strong>
      </p>
      <p className="widget-error-action">
        {action ??
          `${
            httpStatus === null
              ? "The backend did not answer. "
              : `The backend answered with HTTP ${httpStatus}. `
          }Nothing was changed. Use Refresh in the top bar; if it still fails, check that the backend is reachable.`}
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

/** What the widget shows when there is nothing to list or measure, and why. */
function ReasonState({
  lead,
  reason,
  variant,
}: {
  lead?: string;
  reason: string;
  variant: WidgetVariant;
}) {
  return (
    <p className={variant === "kpi" ? "kpi-state" : "tw-state"} role="status">
      {lead ? <strong>{lead} </strong> : null}
      {reason}
    </p>
  );
}

/** "3 h", "45 min": how old stale data is, in the largest whole unit. */
function formatAge(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} min`;
  if (seconds < 48 * 3600) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
}

/** Data that is shown but old: when it is from, so it is never read as current. */
function StaleNote({
  asOf,
  ageSeconds,
  variant,
}: {
  asOf: string;
  ageSeconds: number;
  variant: WidgetVariant;
}) {
  return (
    <p
      className={variant === "kpi" ? "kpi-foot kpi-refresh-failed" : "tw-note warn"}
      role="status"
    >
      Stale: the latest data is from <time dateTime={asOf}>{formatClockUtc(asOf)}</time>,{" "}
      {formatAge(ageSeconds)} old.
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

type Shown<T> =
  | { kind: "loading" }
  | {
      kind: "ok";
      data: T;
      receivedAt: string;
      refreshFailed: { httpStatus: number | null } | null;
    }
  | { kind: "stale"; data: T; receivedAt: string; ageSeconds: number; refreshFailed: null }
  | { kind: "empty" | "no_data" | "not_collected"; reason: string }
  | { kind: "error"; httpStatus: number | null; action?: string }
  | { kind: "forbidden"; permission: string };

/** One shape for the hook's states and the UR.5.4 union. */
function normalize<T>(
  state: ResourceState<T> | WidgetState<T>,
  permission: string,
): Shown<T> {
  if ("kind" in state) {
    switch (state.kind) {
      case "ok":
        return {
          kind: "ok",
          data: state.data,
          receivedAt: state.asOf ?? "",
          refreshFailed: null,
        };
      case "stale":
        return {
          kind: "stale",
          data: state.data,
          receivedAt: state.asOf,
          ageSeconds: state.ageSeconds,
          refreshFailed: null,
        };
      case "error":
        return { kind: "error", httpStatus: null, action: state.action };
      default:
        return state;
    }
  }
  switch (state.status) {
    case "idle":
    case "loading":
      return { kind: "loading" };
    case "forbidden":
      return { kind: "forbidden", permission };
    case "error":
      return { kind: "error", httpStatus: state.httpStatus };
    case "ok":
      return {
        kind: "ok",
        data: state.data,
        receivedAt: state.receivedAt,
        refreshFailed: state.refreshFailed,
      };
  }
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
  const edit = useContext(WidgetEditContext);
  const runtime = useContext(WidgetRuntimeContext);
  const expanded = runtime?.expanded ?? false;
  // The expand dialog holds a second copy of the widget: its ids must not repeat the first's.
  const frameId = expanded ? `${id}-expanded` : id;
  const headingId = `${frameId}-title`;
  const descId = `${frameId}-desc`;
  let body: ReactNode;

  const shown = normalize(state, permission);

  switch (shown.kind) {
    case "loading":
      body = <WidgetLoading what={what} variant={variant} />;
      break;
    case "forbidden":
      body = <ForbiddenState what={what} permission={shown.permission} variant={variant} />;
      break;
    case "error":
      body = (
        <FailedState
          what={what}
          httpStatus={shown.httpStatus}
          action={shown.action}
          variant={variant}
        />
      );
      break;
    case "empty":
      body = <ReasonState reason={shown.reason} variant={variant} />;
      break;
    case "no_data":
      body = <ReasonState lead="No data." reason={shown.reason} variant={variant} />;
      break;
    case "not_collected":
      body = <ReasonState lead="Not collected." reason={shown.reason} variant={variant} />;
      break;
    case "ok":
    case "stale":
      body = (
        <>
          {children({
            data: shown.data,
            receivedAt: shown.receivedAt,
            refreshFailed: shown.refreshFailed,
          })}
          {shown.kind === "stale" ? (
            <StaleNote asOf={shown.receivedAt} ageSeconds={shown.ageSeconds} variant={variant} />
          ) : null}
          <RefreshNote
            failed={shown.refreshFailed}
            receivedAt={shown.receivedAt}
            variant={variant}
          />
        </>
      );
      break;
  }

  const Root = kpi ? "article" : "section";
  // Customize mode keeps one control in the title row (Edit); range, Expand and Configure live
  // in its panel. In the expand dialog the dialog's own header carries the title and the range.
  const showRange = !edit && !expanded && runtime?.rangeOptions;
  const showExpand = !edit && !expanded && runtime?.onExpand;
  const tools = (
    <>
      {showRange && runtime.rangeOptions ? (
        <RangeControl
          options={runtime.rangeOptions}
          value={effectiveRange(runtime.override, runtime.dashboardRange)}
          onChange={runtime.setRange}
          compact={kpi}
          title={title}
        />
      ) : null}
      {showExpand && runtime.onExpand ? (
        <button
          type="button"
          className="tw-expand"
          aria-label={`Expand ${title}`}
          onClick={(event) => runtime.onExpand?.(event.currentTarget)}
        >
          Expand
        </button>
      ) : null}
      {edit?.arrange}
      {!edit ? (
        <Link
          className={kpi ? "kpi-link" : "tw-all"}
          href={link.href}
          aria-label={link.ariaLabel}
        >
          {link.label}
        </Link>
      ) : null}
    </>
  );
  return (
    <Root
      className={`${kpi ? "kpi" : "tw"}${expanded ? " tw-expanded" : ""}`}
      aria-labelledby={expanded ? undefined : headingId}
      aria-label={expanded ? title : undefined}
      aria-describedby={description ? descId : undefined}
    >
      {expanded ? null : (
        <header className={`${kpi ? "kpi-head" : "tw-head"}${edit ? " dg-drag-handle" : ""}`}>
          <h2 className={kpi ? "kpi-title" : "tw-title"} id={headingId}>
            {title}
            {windowText && <span className="tw-window">{windowText}</span>}
          </h2>
          {badge && <span className="pill">{badge}</span>}
          {controls}
          {kpi ? tools : <div className="tw-tools">{tools}</div>}
        </header>
      )}
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
