// The Related activity section of an anomaly page (SOC-3b): what else happened on the anomaly's
// log source around it. Three lists, each with its own question and its own state, because the
// three can disagree (offenses collected and fresh, rules only partly measured, anomalies fine).
//
// It is a server-rendered section that suspends on the read, so the investigation above and below
// it never waits for the related-activity queries. The pad is part of the URL (`?pad=`), so a
// link to this page keeps the window the analyst chose.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Suspense, use, type ReactNode } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type {
  RelatedActivity,
  RelatedAnomaly,
  RelatedOffense,
  RelatedPad,
  RelatedRule,
  RelatedSection,
  RelatedSectionState,
} from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatCount } from "@/lib/format";
import { magnitudeTone, offenseStatusTone } from "@/lib/health";
import { newestCapture, RELATED_PADS, windowText } from "@/lib/related";
import { relativeAgeFromSeconds } from "@/lib/time";

import "@/components/ui/controls.css";
import "./related.css";

/** What the page's loader hands over. It never rejects: a failed read is a value the view words. */
export type RelatedRead =
  | { kind: "ok"; data: RelatedActivity }
  | { kind: "failed"; why: "error" | "notFound" | "forbidden" | "limited"; message?: string };

export interface RelatedActivityProps {
  anomalyId: string;
  pad: RelatedPad;
  read: Promise<RelatedRead>;
  /** The page's clock, for the age of a stale list. */
  nowMs: number;
}

export function RelatedActivitySection({ anomalyId, pad, read, nowMs }: RelatedActivityProps) {
  return (
    <Suspense fallback={<RelatedLoading anomalyId={anomalyId} pad={pad} />}>
      <RelatedResolved anomalyId={anomalyId} pad={pad} read={read} nowMs={nowMs} />
    </Suspense>
  );
}

function PadControl({ anomalyId, pad }: { anomalyId: string; pad: RelatedPad }) {
  const t = useTranslations("related");
  return (
    <nav className="range-selector related-pad" aria-label={t("padLabel")}>
      <span className="muted">{t("padPrefix")}</span>
      {RELATED_PADS.map((choice) => (
        <Link
          key={choice}
          href={`/anomalies/${anomalyId}?pad=${choice}#related`}
          scroll={false}
          className={`range-option${choice === pad ? " range-option-active" : ""}`}
          aria-current={choice === pad ? "page" : undefined}
        >
          {t(`pad.${choice}`)}
        </Link>
      ))}
    </nav>
  );
}

function Frame({
  anomalyId,
  pad,
  title,
  busy = false,
  children,
}: {
  anomalyId: string;
  pad: RelatedPad;
  title: string;
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <section id="related" className="related" aria-labelledby="related-h" aria-busy={busy}>
      <div className="related-head">
        <h2 className="section-title" id="related-h">
          {title}
        </h2>
        <PadControl anomalyId={anomalyId} pad={pad} />
      </div>
      {children}
    </section>
  );
}

function RelatedLoading({ anomalyId, pad }: { anomalyId: string; pad: RelatedPad }) {
  const t = useTranslations("related");
  return (
    <Frame anomalyId={anomalyId} pad={pad} title={t("heading")} busy>
      <p className="subtitle" role="status">
        {t("loading")}
      </p>
    </Frame>
  );
}

function RelatedResolved({ anomalyId, pad, read, nowMs }: RelatedActivityProps) {
  const t = useTranslations("related");
  const result = use(read);

  if (result.kind === "failed") {
    return (
      <Frame anomalyId={anomalyId} pad={pad} title={t("heading")}>
        <div className="notice related-failed" role="alert">
          <p>{result.why === "limited" ? (result.message ?? t("read.error")) : t(`read.${result.why}`)}</p>
        </div>
      </Frame>
    );
  }

  const { data } = result;
  const padText = t(`pad.${data.anchor.pad}`);
  const span = windowText(data.anchor.window_start, data.anchor.window_end);
  const title = span
    ? t("headingWindow", { window: span, pad: padText })
    : t("headingNoWindow", { pad: padText });

  if (data.state === "NOT_APPLICABLE") {
    return (
      <Frame anomalyId={anomalyId} pad={data.anchor.pad} title={title}>
        <p className="banner related-banner" role="status">
          <span>{t("notApplicable")}</span>
        </p>
      </Frame>
    );
  }

  const source = data.anchor.log_source_name;
  return (
    <Frame anomalyId={anomalyId} pad={data.anchor.pad} title={title}>
      <p className="subtitle">
        {source
          ? t("source", { source })
          : t("sourceUnnamed", { id: data.anchor.log_source_id ?? "—" })}
      </p>
      <div className="related-grid">
        <OffensesBlock section={data.offenses} nowMs={nowMs} />
        <div className="related-side">
          <AnomaliesBlock section={data.anomalies} />
          <RulesBlock section={data.rules} />
        </div>
      </div>
    </Frame>
  );
}

// --- one block per list ------------------------------------------------------

type Which = "offenses" | "anomalies" | "rules";

/** "20 of 34" when the list is shorter than its total, else the total. */
function Count({ section }: { section: RelatedSection<unknown> }) {
  const t = useTranslations("related");
  const text =
    section.total > section.items.length
      ? t("countOf", { shown: formatCount(section.items.length), total: formatCount(section.total) })
      : t("count", { total: formatCount(section.total) });
  return <span className="related-count num">{text}</span>;
}

/**
 * The sentence for a section's state, or `null` for an OK one. An unknown state is worded as
 * unknown and its list is withheld (`show` false): a state this build cannot read is not "OK".
 */
function stateNote(
  t: ReturnType<typeof useTranslations<"related">>,
  which: Which,
  section: RelatedSection<unknown>,
  extra: ReactNode = null,
): { tone: "warn" | ""; body: ReactNode; show: boolean } | null {
  const state: RelatedSectionState = section.state;
  const shown = formatCount(section.items.length);
  const total = formatCount(section.total);
  switch (state) {
    case "OK":
      return null;
    case "NOT_COLLECTED":
      return { tone: "warn", body: t(`${which}.NOT_COLLECTED`), show: false };
    case "STALE":
      return { tone: "warn", body: <>{t(`${which}.STALE`)} {extra}</>, show: true };
    case "PARTIAL":
      return { tone: "warn", body: t(`${which}.PARTIAL`), show: true };
    case "TRUNCATED":
      return { tone: "", body: t(`${which}.TRUNCATED`, { shown, total }), show: true };
    case "NOT_APPLICABLE":
      return { tone: "", body: t(`${which}.NOT_APPLICABLE`), show: false };
    default:
      return { tone: "warn", body: t("unknownState", { state: String(state) }), show: false };
  }
}

function Block({
  which,
  section,
  extra,
  children,
}: {
  which: Which;
  section: RelatedSection<unknown>;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const t = useTranslations("related");
  const note = stateNote(t, which, section, extra);
  const listed = note ? note.show : true;
  return (
    <div className="related-block" data-state={section.state}>
      <h3 className="related-title">
        {t(`${which}.heading`)}
        {listed && <Count section={section} />}
      </h3>
      {note && (
        <p className={`banner related-banner ${note.tone}`} role="status">
          <span>{note.body}</span>
        </p>
      )}
      {listed && (section.items.length === 0 ? (
        // An empty list is a real "none" only for an OK section; the other states are worded above.
        section.state === "OK" ? <p className="related-empty">{t(`${which}.empty`)}</p> : null
      ) : (
        children
      ))}
    </div>
  );
}

function OffensesBlock({ section, nowMs }: { section: RelatedSection<RelatedOffense>; nowMs: number }) {
  const t = useTranslations("related");
  const say = asTextTranslator(useTranslations());
  const newest = newestCapture(section.items.map((o) => o.captured_at));
  const age = newest === null ? null : relativeAgeFromSeconds(Math.max(0, (nowMs - newest) / 1000));
  const extra =
    section.state === "STALE"
      ? age && newest !== null
        ? t("offenses.staleAge", { age, time: utcTimeText(new Date(newest).toISOString()) })
        : t("offenses.staleNoAge")
      : null;
  return (
    <Block which="offenses" section={section} extra={extra}>
      <TableScroll label={t("offenses.caption")}>
        <table className="related-table" aria-label={t("offenses.caption")}>
          <thead>
            <tr>
              <th>{t("offenses.col.offense")}</th>
              <th>{t("offenses.col.description")}</th>
              <th className="num">{t("offenses.col.magnitude")}</th>
              <th>{t("offenses.col.status")}</th>
              <th>{t("offenses.col.started")}</th>
              <th>{t("offenses.col.updated")}</th>
            </tr>
          </thead>
          <tbody>
            {section.items.map((o) => (
              <tr key={o.qradar_offense_id}>
                <td className="num">
                  <Link
                    href={`/offenses/${o.qradar_offense_id}`}
                    aria-label={t("offenses.open", { id: o.qradar_offense_id })}
                  >
                    {o.qradar_offense_id}
                  </Link>
                </td>
                <td className="cell-name related-desc">
                  {o.description ?? <span className="muted">{t("offenses.noDescription")}</span>}
                </td>
                <td className="num">
                  {o.magnitude === null ? (
                    <span className="muted">—</span>
                  ) : (
                    <span className={`pill ${magnitudeTone(o.magnitude)}`}>{o.magnitude}</span>
                  )}
                </td>
                <td>
                  {o.status ? (
                    <span className={`pill pill-quiet ${offenseStatusTone(o.status)}`}>
                      {enumLabel(say, "offenseStatus", o.status)}
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <UtcTime value={o.start_time} variant="compact" bare />
                </td>
                <td>
                  <UtcTime value={o.last_updated_time} variant="compact" bare />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </Block>
  );
}

/** The detector's name as an analyst reads it, from the family's own catalog. */
function detectorName(
  a: RelatedAnomaly,
  say: ReturnType<typeof asTextTranslator>,
  customRule: string,
): string {
  if (a.family === "custom_rule") return customRule;
  return enumLabel(say, a.family === "property_watch" ? "propertyWatchAnomalyType" : "anomalyType", a.type);
}

function AnomaliesBlock({ section }: { section: RelatedSection<RelatedAnomaly> }) {
  const t = useTranslations("related");
  const tt = useTranslations("triage");
  const say = asTextTranslator(useTranslations());
  return (
    <Block which="anomalies" section={section}>
      <TableScroll label={t("anomalies.caption")}>
        <table className="related-table" aria-label={t("anomalies.caption")}>
          <thead>
            <tr>
              <th>{t("anomalies.col.anomaly")}</th>
              <th>{t("anomalies.col.state")}</th>
              <th>{t("anomalies.col.triage")}</th>
              <th>{t("anomalies.col.started")}</th>
            </tr>
          </thead>
          <tbody>
            {section.items.map((a) => {
              const name = detectorName(a, say, t("anomalies.customRuleType"));
              return (
                <tr key={`${a.family}-${a.id}`}>
                  <td>
                    {/* A custom-rule breach has no detail page of its own, so it is not a link. */}
                    {a.family === "custom_rule" ? name : <Link href={`/anomalies/${a.id}`}>{name}</Link>}
                    <span className="cell-sub">{t(`anomalies.family.${a.family}`)}</span>
                  </td>
                  <td>
                    <span className={`pill ${stateTone(a.lifecycle_state)}`}>
                      {enumLabel(say, "anomalyState", a.lifecycle_state)}
                    </span>
                  </td>
                  <td>
                    {a.family === "custom_rule" ? (
                      <span className="muted">{t("anomalies.triageNotAvailable")}</span>
                    ) : (
                      <span
                        className={`pill pill-quiet ${
                          a.triage_status === "IN_PROGRESS" ? "info" : a.triage_status === "NEW" ? "warn" : ""
                        }`}
                      >
                        {tt(`status.${a.triage_status}`)}
                      </span>
                    )}
                  </td>
                  <td>
                    <UtcTime value={a.start} variant="compact" bare />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
    </Block>
  );
}

function RulesBlock({ section }: { section: RelatedSection<RelatedRule> }) {
  const t = useTranslations("related");
  return (
    <Block which="rules" section={section}>
      <TableScroll label={t("rules.caption")}>
        <table className="related-table" aria-label={t("rules.caption")}>
          <thead>
            <tr>
              <th>{t("rules.col.rule")}</th>
              <th className="num">{t("rules.col.offenses")}</th>
              <th className="num">{t("rules.col.events")}</th>
            </tr>
          </thead>
          <tbody>
            {section.items.map((r) => (
              <tr key={r.qradar_id}>
                <td className="cell-name">
                  {r.name === null ? (
                    <span className="muted">{t("rules.notInCatalog", { id: r.qradar_id })}</span>
                  ) : (
                    <>
                      {r.id ? <Link href={`/rules/${r.id}`}>{r.name}</Link> : r.name}
                      <span className="cell-sub">{t("rules.qradarId", { id: r.qradar_id })}</span>
                    </>
                  )}
                </td>
                <td className="num">{formatCount(r.related_offenses)}</td>
                <td className="num">
                  {r.matched_events === null ? (
                    <span className="muted" title={t("rules.notMeasuredTitle")}>
                      {t("rules.notMeasured")}
                    </span>
                  ) : r.measurement === "LOWER_BOUND" ? (
                    <span title={t("rules.lowerBoundTitle")}>≥ {formatCount(r.matched_events)}</span>
                  ) : (
                    formatCount(r.matched_events)
                  )}
                  <span className="cell-sub">{t(`rules.measurement.${r.measurement}`)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </Block>
  );
}
