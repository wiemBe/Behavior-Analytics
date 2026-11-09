// "Is this rule's match rate being judged, and is there an open change?" (SOC-6 design §7.3).
//
// One status line per `RuleMatchSectionState`, the backend's own sentence for the reason (a
// catalog code, rendered through the backend-message reader), and the structured facts that
// sentence names: times in UTC, window and sample counts. Incidents are listed whatever the
// state is: an open silence is held while the latest window cannot be judged. No match rate is
// drawn here, and a figure the API left null is the dash with its reason, never 0.
//
// The section has its own read and its own error: the health card above it is not touched, and
// the health verdict is not relabelled.

import Link from "next/link";
import type { ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";

import "@/components/ui/controls.css";
import "./matchAnomalies.css";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type {
  RuleMatchSection as Section,
  RuleMatchSectionIncident,
  RuleMatchSectionState,
} from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatCount, formatShare } from "@/lib/format";
import { useBackendMessage } from "@/i18n/backendMessage";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

import { formatMatches, formatTimes, MISSING } from "./ruleMatchFormat";

const PILL_KEY: Record<RuleMatchSectionState, string> = {
  NOT_COLLECTED: "notCollected",
  NOT_EVENT: "notEvent",
  DISABLED: "disabled",
  MONITORING_OFF: "monitoringOff",
  MODIFIED: "modified",
  TRUNCATED: "truncated",
  INSUFFICIENT_DATA: "insufficient",
  OK: "judgedPill",
};

function Incidents({ incidents }: { incidents: RuleMatchSectionIncident[] }) {
  const t = useTranslations("rules.matchSection");
  const kinds = useTranslations("enums.ruleAnomalyType");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  return (
    <TableScroll label={t("incidentsLabel")}>
      <table className="table-dense match-table">
        <caption className="sr-only">{t("incidentsCaption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("detector")}</th>
            <th scope="col">{t("state")}</th>
            <th scope="col" className="num">
              {t("observed")}
            </th>
            <th scope="col" className="num">
              {t("expected")}
            </th>
            <th scope="col" className="num">
              {t("band")}
            </th>
            <th scope="col" className="num">
              {t("streak")}
            </th>
            <th scope="col" className="num">
              {t("ratio")}
            </th>
            <th scope="col">{t("started")}</th>
            <th scope="col">{t("ended")}</th>
            <th scope="col">
              <span className="sr-only">{t("open")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.anomaly_id}>
              <th scope="row">{kinds.has(i.detector) ? kinds(i.detector) : i.detector}</th>
              <td>
                <span className={`pill pill-strong ${stateTone(i.state)}`}>
                  {enumLabel(say, "anomalyState", i.state)}
                </span>
              </td>
              <td className="num">{formatMatches(i.observed, locale)}</td>
              <td className="num">{formatMatches(i.expected, locale)}</td>
              <td className="num">
                {i.baseline_low === null || i.baseline_high === null
                  ? MISSING
                  : t("bandValue", {
                      low: formatMatches(i.baseline_low, locale),
                      high: formatMatches(i.baseline_high, locale),
                    })}
              </td>
              <td className="num">{i.consecutive_buckets === null ? MISSING : formatCount(i.consecutive_buckets, locale)}</td>
              <td className="num">{formatTimes(i.ratio, locale)}</td>
              <td>
                <UtcTime value={i.started} variant="compact" wrapZone />
              </td>
              <td>
                {i.ended ? (
                  <UtcTime value={i.ended} variant="compact" wrapZone />
                ) : (
                  <span className="muted">{t("stillOpen")}</span>
                )}
              </td>
              <td>
                <Link href={`/anomalies/${i.anomaly_id}`}>{t("open")}</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function Facts({ section }: { section: Section }) {
  const t = useTranslations("rules.matchSection");
  const locale = useLocale() as Locale;
  const c = section.counts;
  const rows: Array<[string, ReactNode]> = [];

  if (section.match_evidence) {
    rows.push([
      t("evidence"),
      section.match_evidence === "LOWER_BOUND"
        ? t("evidenceLower")
        : section.match_evidence === "NOT_MEASURED"
          ? t("evidenceNotMeasured")
          : section.match_evidence,
    ]);
  }
  if (section.state === "DISABLED" && section.inventory_synced_at) {
    rows.push([t("inventorySynced"), <UtcTime key="sync" value={section.inventory_synced_at} />]);
  } else if (section.inventory_synced_at && section.state === "INSUFFICIENT_DATA") {
    rows.push([t("inventorySynced"), <UtcTime key="sync" value={section.inventory_synced_at} />]);
  }
  if (section.modified_at) {
    rows.push([t("modifiedAt"), <UtcTime key="mod" value={section.modified_at} />]);
  }
  if (section.window_start && section.window_end) {
    rows.push([
      t("windowSpan"),
      <span key="win">
        <UtcTime value={section.window_start} /> {t("windowSpanTo")} <UtcTime value={section.window_end} />
      </span>,
    ]);
  }
  if (c) {
    if (c.expected_windows != null && c.complete_windows != null) {
      rows.push([
        t("windows"),
        t("windowsValue", {
          complete: formatCount(c.complete_windows, locale),
          expected: formatCount(c.expected_windows, locale),
        }),
      ]);
    }
    if (c.missing_windows != null && c.truncated_windows != null && c.failed_windows != null) {
      rows.push([
        t("windowGaps"),
        t("windowGapsValue", {
          missing: formatCount(c.missing_windows, locale),
          truncated: formatCount(c.truncated_windows, locale),
          failed: formatCount(c.failed_windows, locale),
        }),
      ]);
    }
    if (c.wrong_width_windows != null && c.wrong_width_windows > 0) {
      rows.push([t("wrongWidth"), t("wrongWidthValue", { count: c.wrong_width_windows })]);
    }
    if (c.presence != null && c.presence_minimum != null) {
      rows.push([
        t("presence"),
        t("presenceValue", {
          share: formatShare(c.presence, locale),
          minimum: formatShare(c.presence_minimum, locale),
        }),
      ]);
    }
    if (c.baseline_days != null) {
      rows.push([t("baselineDays"), t("baselineDaysValue", { count: c.baseline_days })]);
    }
    if (c.sample_count != null && c.minimum_samples != null) {
      rows.push([
        t("samples"),
        t("samplesValue", {
          count: formatCount(c.sample_count, locale),
          minimum: formatCount(c.minimum_samples, locale),
        }),
      ]);
    }
    if (c.visits != null && c.minimum_visits != null) {
      rows.push([
        t("weeks"),
        t("weeksValue", {
          count: formatCount(c.visits, locale),
          minimum: formatCount(c.minimum_visits, locale),
        }),
      ]);
    }
    if (c.median != null && c.median_minimum != null) {
      rows.push([
        t("median"),
        t("medianValue", {
          median: formatMatches(c.median, locale),
          minimum: formatMatches(c.median_minimum, locale),
        }),
      ]);
    }
  }
  // The OK sentence already says "Judged through T"; the fact row is for the other states.
  if (section.judged_through && section.state !== "OK") {
    rows.push([t("judgedThrough"), <UtcTime key="through" value={section.judged_through} />]);
  }
  if (rows.length === 0) return null;
  return (
    <dl className="match-facts">
      {rows.map(([name, value], index) => (
        <div key={index} className="match-fact">
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RuleMatchSectionView({
  section,
  failed,
}: {
  /** The read's answer; null when it failed. */
  section: Section | null;
  failed: boolean;
}) {
  const t = useTranslations("rules.matchSection");
  const backend = useBackendMessage();

  let body;
  if (failed || section === null) {
    body = (
      <div className="notice" role="alert">
        <p>
          <strong>{t("failedTitle")}</strong>
        </p>
        <p>{t("failedHelp")}</p>
      </div>
    );
  } else {
    const reason =
      section.reason === null
        ? null
        : backend({ text: section.reason, code: section.reason_code, params: section.reason_params });
    body = (
      <>
        <p className="match-status">
          <span className={`pill ${section.state === "OK" ? "ok" : ""}`}>{t(PILL_KEY[section.state] as never)}</span>
          <span className="match-status-text">{reason ?? t("noReason")}</span>
        </p>
        <Facts section={section} />
        <div className="match-incidents">
          <h4>{t("incidents")}</h4>
          {section.incidents.length === 0 ? (
            <p className="muted">
              {section.state === "OK" ? t("incidentsNone") : t("incidentsNoneNotJudged")}
            </p>
          ) : (
            <Incidents incidents={section.incidents} />
          )}
        </div>
      </>
    );
  }

  return (
    <section className="match-section" aria-labelledby="rule-match-heading">
      <h3 id="rule-match-heading">{t("heading")}</h3>
      <p className="subtitle">{t("question")}</p>
      {body}
    </section>
  );
}
