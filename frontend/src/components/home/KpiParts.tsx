
import { formatCount, type FormatLocale } from "@/lib/format";

// The parts a KPI card's body is built from: a figure with its unit and a line
// of breakdown parts. The frame and its honesty qualifiers are in `WidgetFrame`.

export const MISSING = "—";

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

/**
 * Parts of one line, set off by a middle dot. Empty parts are dropped. The dot stays with the
 * part before it, so a wrapped line never starts with a separator.
 */
export function Line({
  parts,
  title,
  label,
}: {
  parts: (string | null | false | undefined)[];
  title?: string;
  /** Names the dimension when adjacent lines are different breakdowns. */
  label?: string;
}) {
  const shown = parts.filter((p): p is string => !!p);
  if (shown.length === 0) return null;
  return (
    <p className="kpi-line num" title={title}>
      {label ? <span className="kpi-line-label">{label}: </span> : null}
      {shown.map((part, index) => (
        <span key={`${index}-${part}`}>
          <span className="kpi-line-part">
            {part}
            {index < shown.length - 1 ? " \u00b7" : ""}
          </span>
          {index < shown.length - 1 ? " " : ""}
        </span>
      ))}
    </p>
  );
}

/** A count and the noun it measures, kept together when the line wraps. */
export function countNoun(value: number, noun: string, locale: FormatLocale = "en"): string {
  return `${formatCount(value, locale)}\u00a0${noun}`;
}
