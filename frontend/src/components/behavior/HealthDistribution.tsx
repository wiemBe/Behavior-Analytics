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

import type { TextTranslator } from "@/i18n/textTranslator";
import type { HealthGroup } from "@/lib/behaviorOverview";

/** The empty notice without a translator (the Behavior page, not converted yet). */
const NO_SOURCES =
  "No monitored log sources. Enable monitoring on a source to start collecting its volume baseline.";

export function HealthDistribution({
  groups,
  total,
  summary,
  say,
}: {
  groups: HealthGroup[];
  total: number;
  /** The distribution as a sentence, from `healthSummaryText`. */
  summary: string;
  /**
   * The interface-language translator. Without it the component keeps its English (the Behavior
   * page, which is not converted yet, passes none); with it the group words come from the catalog
   * by the group's key, and a group the catalog lacks keeps the label the derivation gave it.
   */
  say?: TextTranslator;
}) {
  const word = (g: HealthGroup) => {
    const key = `home.healthGroups.${g.key}`;
    return say?.has?.(key) ? say(key) : g.label;
  };
  if (total === 0 || groups.length === 0) {
    return (
      <div className="notice">
        {say ? say("home.healthGroups.noSources") : NO_SOURCES}
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
            <title>{[word(g), g.count].join(": ")}</title>
          </rect>
        ))}
      </svg>

      <ul className="health-legend">
        {groups.map((g) => (
          <li key={g.key}>
            <span className={`health-dot ${g.tone}`} aria-hidden="true" />
            <span className="health-count num">{g.count}</span>
            <span className="muted">{word(g)}</span>
          </li>
        ))}
      </ul>

      <p className="sr-only">{summary}</p>
    </div>
  );
}
