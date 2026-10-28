import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import Link from "next/link";
// The values table of a watch page: every figure the chart carries, as text, so assistive
// technology and a reader who cannot see the canvas lose nothing (guidelines §10).
//
// Value text is attacker-controlled (a username from a log line). React renders every cell as
// text; nothing here is markup. Numbers are right-aligned with tabular figures and carry their
// unit in the column header. A reading that is not a number says what it is in words.
//
// The baseline state and the open incidents sit under the value, not in a column of their own:
// at 1024 px a seventh column was clipped, and they describe the value, not a measurement.
// A long value takes two lines; the full text is behind a disclosure, which a keyboard reaches.

import type { AnomalySummary, PwBucket, PwValue, PwWatchHistory } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { useEnumLabels } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

import {
  baselineText,
  eventsUnit,
  expectedRangeText,
  formatShare,
  pwTypeLabel,
  readingMeaning,
  readingText,
  shareOfParsed,
} from "./pwFormat";
import { ENGLISH_PW, type PwI18n } from "./pwText";

/** A value longer than this is cut to two lines in the cell and offered in full on request. */
const FULL_VALUE_FROM = 60;

export type ValueSort = "events" | "value" | "first_seen";

/** The value as an analyst reads it: a combination's components joined, else its text. */
export function valueText(
  v: Pick<PwValue, "value_key" | "components">,
  _i18n: PwI18n = ENGLISH_PW,
): string {
  void _i18n;
  return v.components.length > 1 ? v.components.join(" × ") : v.value_key;
}

export const ACTIVE_STATES = new Set(["CANDIDATE", "OPEN", "RECOVERING"]);

/** Active incidents by the value they are about. Keyed by the value's text, as the list is. */
export function activeIncidentsByValue(items: AnomalySummary[]): Map<string, AnomalySummary[]> {
  const map = new Map<string, AnomalySummary[]>();
  for (const a of items) {
    if (!a.pw_value || !ACTIVE_STATES.has(a.state)) continue;
    const list = map.get(a.pw_value.value_key) ?? [];
    list.push(a);
    map.set(a.pw_value.value_key, list);
  }
  return map;
}

export function WatchValuesTable({
  values,
  latest,
  selected,
  maxSelected,
  onToggle,
  incidents,
  sort,
  sortHref,
  history = null,
}: {
  values: PwValue[];
  /** The newest collected bucket, for the share column. */
  latest: PwBucket | null;
  selected: ReadonlySet<string>;
  maxSelected: number;
  onToggle: (valueId: string) => void;
  incidents: Map<string, AnomalySummary[]>;
  sort: ValueSort;
  /** The link a sortable header points to. */
  sortHref: (sort: ValueSort) => string;
  history?: PwWatchHistory | null;
}) {
  const t = useTranslations("propertyWatch.valuesTable");
  const i18n = useBehaviorI18n();
  const { statePill } = useEnumLabels();
  const unit = eventsUnit(latest?.bucket_seconds ?? 300, i18n);
  const full = selected.size >= maxSelected;
  const header = (key: ValueSort, text: ReactNode, className?: string) => (
    <th
      scope="col"
      className={className}
      aria-sort={sort === key ? (key === "events" ? "descending" : "ascending") : "none"}
    >
      <Link className="sort-link" href={sortHref(key)}>
        {text}
      </Link>
    </th>
  );
  return (
    <table className="table-dense pw-values" aria-label={t("label")}>
      <caption className="sr-only">
        {t("captionStart")}
        {latest ? (
          <>
            {" "}
            <UtcTime value={latest.bucket_start} />
          </>
        ) : (
          t("captionUnavailable")
        )}
        {t("captionCounts", { unit })}
        {history?.floor ? (
          t.rich("captionHistory", { time: () => <UtcTime value={history.floor as string} /> })
        ) : (
          ""
        )}
        {t("captionSelect", { max: maxSelected })}
      </caption>
      <thead>
        <tr>
          <th scope="col" className="pw-col-plot">
            <span className="sr-only">{t("plot")}</span>
          </th>
          {header("value", t("sortValue"))}
          {header("events", t("eventsLatest"), "num")}
          <th scope="col" className="num">
            {t("expectedRange")}
          </th>
          <th scope="col" className="num">
            {t("share")}
          </th>
          {header(
            "first_seen",
            history?.floor ? (
              <>
                {t.rich("firstSeenHistory", {
                  time: () => <UtcTime value={history.floor as string} />,
                })}
              </>
            ) : (
              t("firstSeen")
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {values.map((v) => {
          const text = valueText(v, i18n);
          const checked = selected.has(v.id);
          const open = incidents.get(v.value_key) ?? [];
          return (
            <tr key={v.id} aria-selected={checked || undefined}>
              <td className="pw-col-plot">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && full}
                  onChange={() => onToggle(v.id)}
                  aria-label={t("plotValue", { value: text })}
                />
              </td>
              <td className="cell-name pw-value-cell">
                <span className="pw-value-text pw-value-clamp">{text}</span>
                {text.length > FULL_VALUE_FROM && (
                  <details className="pw-value-full">
                    <summary>{t("showFull")}</summary>
                    {/* Attacker-controlled: rendered as text, never as markup. */}
                    <span className="pw-value-text">{text}</span>
                  </details>
                )}
                {v.pinned && <span className="cell-sub">{t("pinned")}</span>}
                {v.truncated && <span className="cell-sub">{t("truncated")}</span>}
                {!v.active && <span className="cell-sub">{t("inactive")}</span>}
                <span className="cell-sub">{baselineText(v, i18n)}</span>
                {open.map((a) => (
                  <span key={a.id} className="cell-sub">
                    <span className={`pill pill-quiet ${stateTone(a.state)}`}>
                      {pwTypeLabel(a.anomaly_type, i18n)} · {statePill(a.state)}
                    </span>
                  </span>
                ))}
              </td>
              <td className="num" title={readingMeaning(v.reading, i18n)}>
                {readingText(v.reading, v.events, v.measurement, i18n)}
              </td>
              <td className="num">{expectedRangeText(v, i18n)}</td>
              <td className="num">{formatShare(shareOfParsed(v, latest), i18n.locale)}</td>
              <td className="num cell-when nowrap">
                {v.first_seen_at ? (
                  <UtcTime value={v.first_seen_at} variant="compact" />
                ) : (
                  "—"
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
