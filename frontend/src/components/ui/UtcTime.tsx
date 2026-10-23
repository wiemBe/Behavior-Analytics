// The one way to show an instant.
//
// Every formatted timestamp that reaches the screen goes through here, so the
// text, the zone and the `<time dateTime>` stay the same wherever it appears.
// Compact text is `10-12 09:15 UTC` and titles the full instant. A relative
// age, when the caller has one, is rendered after the absolute time and never
// instead of it. A missing or unparsable instant is an em dash and not a
// `<time>`: there is no datetime to attach.

import type { ReactNode } from "react";

import { formatDateTime, formatDateTimeCompact } from "@/lib/health";

const MISSING = "—";

export type UtcTimeVariant = "full" | "compact";

/** The formatter's text. `bare` drops a trailing " UTC" where a header already names the zone. */
export function utcTimeText(
  value: string | null | undefined,
  variant: UtcTimeVariant = "full",
  bare = false,
): string {
  const text = variant === "compact" ? formatDateTimeCompact(value) : formatDateTime(value);
  if (bare && text.endsWith(" UTC")) return text.slice(0, -" UTC".length);
  return text;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * A chart tooltip is an HTML string, not a React tree. This is the same
 * instant as `<UtcTime>`, as a `<time>` element the tooltip can contain.
 */
export function utcTimeHtml(
  value: string | null | undefined,
  variant: UtcTimeVariant = "full",
): string {
  const text = utcTimeText(value, variant);
  if (!value || text === MISSING || Number.isNaN(Date.parse(value))) return MISSING;
  const full = utcTimeText(value, "full");
  const title = text === full ? "" : ` title="${escapeHtml(full)}"`;
  return `<time datetime="${escapeHtml(value)}"${title}>${escapeHtml(text)}</time>`;
}

function visibleText(
  value: string | null | undefined,
  variant: UtcTimeVariant,
  bare: boolean,
  wrapZone: boolean,
): ReactNode {
  const text = utcTimeText(value, variant, bare);
  if (!wrapZone || bare) return text;
  const cut = text.lastIndexOf(" ");
  if (cut < 0) return text;
  return (
    <>
      <span className="nowrap">{text.slice(0, cut)}</span> {text.slice(cut + 1)}
    </>
  );
}

export function UtcTime({
  value,
  variant = "full",
  bare = false,
  age,
  ageClassName = "muted",
  className,
  wrapZone = false,
  children,
}: {
  value: string | null | undefined;
  /** `full` is `2026-10-12 09:15:00 UTC`. `compact` is `10-12 09:15 UTC`. */
  variant?: UtcTimeVariant;
  /** Compact text without the zone, for a column whose header already says UTC. The title keeps it. */
  bare?: boolean;
  /** A relative age drawn after the absolute time. The missing mark is not drawn. */
  age?: string | null;
  ageClassName?: string;
  className?: string;
  /** Keep the date and clock together and let the zone wrap, in a narrow cell. */
  wrapZone?: boolean;
  /** A visible label other than the formatter's text. The title stays the full UTC instant. */
  children?: ReactNode;
}) {
  const full = utcTimeText(value, "full");
  if (!value || full === MISSING || Number.isNaN(Date.parse(value))) {
    return <span className="muted">{MISSING}</span>;
  }
  const shown = children ?? visibleText(value, variant, bare, wrapZone);
  const titled = variant === "compact" || bare || wrapZone || children != null;
  const ageText = age && age !== MISSING ? age : null;
  return (
    <>
      <time
        dateTime={value}
        className={className}
        title={titled ? full : undefined}
      >
        {shown}
      </time>
      {ageText ? (
        <>
          {" "}
          <span className={ageClassName}>{ageText}</span>
        </>
      ) : null}
    </>
  );
}
