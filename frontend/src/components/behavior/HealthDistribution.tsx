// The fleet's behavioral states as one compact bar.
//
// Deliberately not a chart. The four KPI cards above it already carry the
// counts that matter individually; what they cannot show is proportion — how
// much of the fleet is in each state at once. A single stacked bar answers
// that in one line, and anything more elaborate would be decoration restating
// numbers the reader already has.
//
// The bar is not the only representation of the data: the same figures are
// listed as text beneath it, and a sentence summarising the whole distribution
// is available to assistive technology. Nothing here is conveyed by colour
// alone.

import type { HealthGroup } from "@/lib/behaviorOverview";

export function HealthDistribution({
  groups,
  total,
  summary,
}: {
  groups: HealthGroup[];
  total: number;
  /** The distribution as a sentence, from `healthSummaryText`. */
  summary: string;
}) {
  if (total === 0 || groups.length === 0) {
    return (
      <div className="notice">
        No monitored log sources. Enable monitoring on a source to start
        collecting its volume baseline.
      </div>
    );
  }

  return (
    <div>
      {/* The visual bar carries no information the list below lacks, so it is
          hidden from assistive technology rather than read out as a row of
          meaningless generic elements. */}
      {/* Drawn as SVG so each segment's width is an attribute rather than an
          inline style. The viewBox is the fleet size, so a segment's width is
          simply its count. */}
      <svg
        className="health-bar"
        viewBox={`0 0 ${Math.max(1, groups.reduce((n, g) => n + g.count, 0))} 1`}
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        {groups.map((g, i) => (
          <rect
            key={g.key}
            className={`health-seg ${g.tone}`}
            x={groups.slice(0, i).reduce((n, h) => n + h.count, 0)}
            y={0}
            width={g.count}
            height={1}
          >
            <title>{`${g.label}: ${g.count}`}</title>
          </rect>
        ))}
      </svg>

      <ul className="health-legend">
        {groups.map((g) => (
          <li key={g.key}>
            <span className={`health-dot ${g.tone}`} aria-hidden="true" />
            <span className="health-count num">{g.count}</span>
            <span className="muted">{g.label}</span>
          </li>
        ))}
      </ul>

      <p className="sr-only">{summary}</p>
    </div>
  );
}
