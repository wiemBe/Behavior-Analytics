// The four blocks of the report (SOC-8c). Each block renders its own state: a FORBIDDEN block names
// its permission, a block that could not be read says so, and neither hides a neighbour. A figure
// that is not measured is an em dash with its state, never a zero. A family whose retention does
// not reach back to the period's start shows lower bounds ("≥ 12"), and a sentence says why.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { ExportCsv } from "@/components/listview/ExportCsv";
import { TableScroll } from "@/components/ui/TableScroll";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  alertsCsvUrl,
  anomaliesCsvUrl,
  type ActiveFamily,
  type ReportAlertsBlock,
  type ReportAnomaliesBlock,
  type ReportAnomalyClocksBlock,
  type ReportBlockBase,
  type ReportClock,
  type ReportFamilyCoverage,
  type ReportNoiseBlock,
} from "@/lib/api";
import { ALERT_FAMILIES, ALERT_SEVERITIES, type AlertFamilyValue } from "@/lib/alertQuery";
import { formatCount, formatShare, type FormatLocale } from "@/lib/format";
import {
  MIN_FIGURE_N,
  alertsDrillHref,
  anomaliesDrillHref,
  periodExportParams,
} from "@/lib/reports";
import { stampText } from "@/lib/timeRange";
import { formatDuration } from "@/lib/time";

import "./reports.css";

/** What the API names when a block's own statement failed (`REASON_BLOCK_FAILED`). */
const BLOCK_FAILED = "reports.reason.block_failed";

export interface ReportRange {
  start: string;
  end: string;
}

const ANOMALY_FAMILIES: readonly ActiveFamily[] = [
  "log_source",
  "waf",
  "ips",
  "property_watch",
  "rule",
];

/** A count that is a lower bound reads "≥ 12". */
function bound(lower: boolean, text: string): string {
  return lower ? `≥ ${text}` : text;
}

function useLocaleKey(): FormatLocale {
  return (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
}

/** The block's frame: heading, state mark, optional export, then the body or the reason there is none. */
function Frame({
  id,
  title,
  block,
  permission,
  available,
  actions,
  children,
}: {
  id: string;
  title: string;
  block: ReportBlockBase;
  /** The permission this block needs, when the API did not name it. */
  permission: string;
  /** The block carries its figures: false when it is FORBIDDEN or its read failed. */
  available: boolean;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const t = useTranslations("reports");
  let body: ReactNode = children;
  if (block.state === "FORBIDDEN") {
    body = (
      <p className="notice rp-notice" role="status">
        {t.rich("forbidden", {
          permission: block.permission ?? permission,
          code: (chunks) => <code>{chunks}</code>,
        })}
      </p>
    );
  } else if (!available) {
    body = (
      <p className="notice rp-notice" role="status">
        {block.reason_code === BLOCK_FAILED
          ? t("blockFailed")
          : block.reason_code
            ? t("blockUnknown", { code: block.reason_code })
            : t("blockNoReason")}
      </p>
    );
  }
  const mark =
    block.state === "PARTIAL"
      ? { tone: "warn", text: t("state.partial") }
      : block.state === "INSUFFICIENT_DATA"
        ? { tone: "warn", text: t("state.insufficient") }
        : block.state === "FORBIDDEN"
          ? { tone: "crit", text: t("state.forbidden") }
          : null;
  return (
    <section className="rp-block" aria-labelledby={id} data-block-state={block.state}>
      <div className="rp-block-head">
        <h2 id={id} className="rp-h">
          {title}
        </h2>
        {mark && <span className={`pill ${mark.tone}`}>{mark.text}</span>}
        {block.state !== "FORBIDDEN" && actions && <div className="rp-actions">{actions}</div>}
      </div>
      {body}
    </section>
  );
}

/** One sentence per family the retention does not fully cover. */
function CoverageNotes({ coverage }: { coverage: ReportFamilyCoverage[] | null }) {
  const t = useTranslations("reports");
  const rows = (coverage ?? []).filter((c) => c.state !== "OK");
  if (rows.length === 0) return null;
  return (
    <ul className="rp-notes">
      {rows.map((c) => {
        const family = t(`family.${c.family}`);
        const days = formatCount(c.retention_days);
        const text =
          c.state === "INSUFFICIENT_DATA"
            ? t("retention.insufficient", { family, days })
            : c.covered_from
              ? t("retention.partial", { family, days, from: stampText(c.covered_from, true) })
              : t("retention.partialNoDate", { family, days });
        return (
          <li key={c.family} data-coverage={c.family} className="muted">
            {text}
          </li>
        );
      })}
    </ul>
  );
}

function SeverityHeads() {
  const say = asTextTranslator(useTranslations());
  return (
    <>
      {ALERT_SEVERITIES.map((s) => (
        <th key={s} scope="col" className="num">
          {enumLabel(say, "severityPill", s)}
        </th>
      ))}
    </>
  );
}

function Count({
  value,
  lowerBound,
  href,
  label,
}: {
  value: number | undefined;
  lowerBound: boolean;
  href: string | null;
  label: string;
}) {
  const t = useTranslations("reports.table");
  const locale = useLocaleKey();
  // Measured zero only in a fully covered family; in a partial one nothing is claimed.
  if (value === undefined) {
    return lowerBound ? (
      <span className="muted" title={t("lowerBound", { count: 0 })}>
        —
      </span>
    ) : (
      <span className="muted">{formatCount(0, locale)}</span>
    );
  }
  const text = bound(lowerBound, formatCount(value, locale));
  if (value === 0 || !href) {
    return value === 0 ? <span className="muted">{text}</span> : <>{text}</>;
  }
  return (
    <Link href={href} aria-label={label} title={lowerBound ? t("lowerBound", { count: value }) : undefined}>
      {text}
    </Link>
  );
}

// ---------------------------------------------------------------------------------- clocks

function ClockTable({
  mtta,
  mttr,
  anomaly,
}: {
  mtta: ReportClock | null;
  mttr: ReportClock | null;
  anomaly: boolean;
}) {
  const t = useTranslations("reports.clock");
  const locale = useLocaleKey();
  const row = (key: "mtta" | "mttr", clock: ReportClock | null) => {
    const label = t(anomaly ? (`${key}Anomaly` as "mttaAnomaly" | "mttrAnomaly") : key);
    const median = clock?.median_seconds ?? null;
    const basis =
      clock === null || clock.state === "INSUFFICIENT_DATA"
        ? t("insufficient", { min: MIN_FIGURE_N })
        : clock.state === "PARTIAL"
          ? t("partial")
          : t("measured");
    return (
      <tr key={key} data-clock={key}>
        <th scope="row">{label}</th>
        <td className="num">{median === null ? "—" : formatDuration(median)}</td>
        <td className="num">{clock ? formatCount(clock.n, locale) : "—"}</td>
        <td className={median === null ? "rp-basis" : "rp-basis muted"}>{basis}</td>
      </tr>
    );
  };
  return (
    <TableScroll label={t("caption")}>
      <table className="table-dense rp-table" aria-label={t("caption")}>
        <caption className="rp-caption">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("measure")}</th>
            <th scope="col" className="num">
              {t("median")}
            </th>
            <th scope="col" className="num">
              {t("n")}
            </th>
            <th scope="col">{t("basis")}</th>
          </tr>
        </thead>
        <tbody>
          {row("mtta", mtta)}
          {row("mttr", mttr)}
        </tbody>
      </table>
    </TableScroll>
  );
}

// ---------------------------------------------------------------------------------- alerts

export function AlertsBlock({ block, range }: { block: ReportAlertsBlock; range: ReportRange }) {
  const t = useTranslations("reports");
  const th = useTranslations("reports.table");
  const locale = useLocaleKey();
  const say = asTextTranslator(useTranslations());
  const rows = block.rows;
  const known = (f: string | null): f is string =>
    f !== null && (ALERT_FAMILIES as readonly string[]).includes(f);

  // A family -> severity -> count grid. Duplicate cells add; a null family is "other".
  const grid = new Map<string | null, Map<string, number>>();
  for (const r of rows ?? []) {
    const cells = grid.get(r.family) ?? new Map<string, number>();
    cells.set(r.severity, (cells.get(r.severity) ?? 0) + r.count);
    grid.set(r.family, cells);
  }
  const order = [...grid.keys()].sort((a, b) => {
    const rank = (f: string | null) =>
      f === null ? 999 : known(f) ? ALERT_FAMILIES.indexOf(f as (typeof ALERT_FAMILIES)[number]) : 500;
    return rank(a) - rank(b);
  });

  return (
    <Frame
      id="rp-alerts"
      title={t("alertsBlock.title")}
      block={block}
      permission="read:alerts"
      available={rows !== null}
      actions={<ExportCsv href={alertsCsvUrl(periodExportParams(range))} label={t("export.alerts")} />}
    >
      {rows !== null && (
        <>
          {rows.length === 0 ? (
            <p className="notice rp-notice" role="status">
              {t("alertsBlock.empty")}
            </p>
          ) : (
            <TableScroll label={t("alertsBlock.caption")}>
              <table className="table-dense rp-table" aria-label={t("alertsBlock.caption")}>
                <caption className="rp-caption">{t("alertsBlock.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{th("family")}</th>
                    <SeverityHeads />
                    <th scope="col" className="num">
                      {th("total")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {order.map((family) => {
                    const cells = grid.get(family)!;
                    const total = [...cells.values()].reduce((a, b) => a + b, 0);
                    const label = family === null ? t("family.other") : known(family) ? t(`family.${family as AlertFamilyValue}`) : family;
                    return (
                      <tr key={family ?? "other"} data-family={family ?? "other"}>
                        <th scope="row" title={family === null ? t("otherHint") : undefined}>
                          {label}
                        </th>
                        {ALERT_SEVERITIES.map((s) => (
                          <td key={s} className="num" data-severity={s}>
                            <Count
                              value={cells.get(s)}
                              lowerBound={false}
                              href={known(family) ? alertsDrillHref({ family, severity: s }, range) : null}
                              label={th("drill", {
                                count: formatCount(cells.get(s) ?? 0, locale),
                                severity: enumLabel(say, "severityPill", s),
                                family: label,
                              })}
                            />
                          </td>
                        ))}
                        <td className="num">{formatCount(total, locale)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}
          <ClockTable mtta={block.mtta} mttr={block.mttr} anomaly={false} />
        </>
      )}
    </Frame>
  );
}

// ------------------------------------------------------------------------------- anomalies

export function AnomaliesBlock({
  block,
  range,
}: {
  block: ReportAnomaliesBlock;
  range: ReportRange;
}) {
  const t = useTranslations("reports");
  const th = useTranslations("reports.table");
  const locale = useLocaleKey();
  const say = asTextTranslator(useTranslations());
  const rows = block.rows;
  const coverage = new Map((block.coverage ?? []).map((c) => [c.family, c.state]));

  const grid = new Map<ActiveFamily, Map<string, number>>();
  for (const r of rows ?? []) {
    const cells = grid.get(r.family) ?? new Map<string, number>();
    cells.set(r.severity, (cells.get(r.severity) ?? 0) + r.count);
    grid.set(r.family, cells);
  }
  const families = ANOMALY_FAMILIES.filter((f) => grid.has(f) || coverage.has(f));

  return (
    <Frame
      id="rp-anomalies"
      title={t("anomaliesBlock.title")}
      block={block}
      permission="read:anomalies"
      available={rows !== null}
      actions={
        <ExportCsv href={anomaliesCsvUrl(periodExportParams(range))} label={t("export.anomalies")} />
      }
    >
      {rows !== null && (
        <>
          {rows.length === 0 && (block.coverage ?? []).every((c) => c.state === "OK") ? (
            <p className="notice rp-notice" role="status">
              {t("anomaliesBlock.empty")}
            </p>
          ) : (
            <TableScroll label={t("anomaliesBlock.caption")}>
              <table className="table-dense rp-table" aria-label={t("anomaliesBlock.caption")}>
                <caption className="rp-caption">{t("anomaliesBlock.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{th("family")}</th>
                    <SeverityHeads />
                    <th scope="col" className="num">
                      {th("totalAnomalies")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {families.map((family) => {
                    const state = coverage.get(family) ?? "OK";
                    const cells = grid.get(family) ?? new Map<string, number>();
                    const label = t(`family.${family}`);
                    if (state === "INSUFFICIENT_DATA") {
                      return (
                        <tr key={family} data-family={family} data-coverage-state={state}>
                          <th scope="row">{label}</th>
                          <td colSpan={ALERT_SEVERITIES.length + 1} className="muted">
                            {t("state.insufficient")}
                          </td>
                        </tr>
                      );
                    }
                    const lower = state === "PARTIAL";
                    const total = [...cells.values()].reduce((a, b) => a + b, 0);
                    return (
                      <tr key={family} data-family={family} data-coverage-state={state}>
                        <th scope="row">{label}</th>
                        {ALERT_SEVERITIES.map((s) => (
                          <td key={s} className="num" data-severity={s}>
                            <Count
                              value={cells.get(s)}
                              lowerBound={lower}
                              href={anomaliesDrillHref({ family, severity: s }, range)}
                              label={th("drill", {
                                count: formatCount(cells.get(s) ?? 0, locale),
                                severity: enumLabel(say, "severityPill", s),
                                family: label,
                              })}
                            />
                          </td>
                        ))}
                        <td className="num">{bound(lower, formatCount(total, locale))}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}
          <CoverageNotes coverage={block.coverage} />
        </>
      )}
    </Frame>
  );
}

// ------------------------------------------------------------------------------ anomaly clocks

export function AnomalyClocksBlock({ block }: { block: ReportAnomalyClocksBlock }) {
  const t = useTranslations("reports");
  const available = block.mtta !== null || block.mttr !== null;
  return (
    <Frame
      id="rp-clocks"
      title={t("anomalyClocks.title")}
      block={block}
      permission="read:anomalies"
      available={available}
    >
      <ClockTable mtta={block.mtta} mttr={block.mttr} anomaly />
      <CoverageNotes coverage={block.coverage} />
    </Frame>
  );
}

// ------------------------------------------------------------------------------------- noise

/** A detector's label: the anomaly-type tables hold WAF, IPS, rule and watch types apart. */
function detectorLabel(say: ReturnType<typeof asTextTranslator>, type: string): string {
  for (const group of ["anomalyType", "ruleAnomalyType", "propertyWatchAnomalyType"] as const) {
    const label = enumLabel(say, group, type);
    if (label !== type) return label;
  }
  return type;
}

export function NoiseBlock({ block }: { block: ReportNoiseBlock }) {
  const t = useTranslations("reports");
  const tn = useTranslations("reports.noise");
  const locale = useLocaleKey();
  const say = asTextTranslator(useTranslations());
  const rows = block.rows;
  const coverage = new Map((block.coverage ?? []).map((c) => [c.family, c.state]));
  return (
    <Frame id="rp-noise" title={tn("title")} block={block} permission="read:anomalies" available={rows !== null}>
      {rows !== null && (
        <>
          {rows.length === 0 ? (
            <p className="notice rp-notice" role="status">
              {tn("empty")}
            </p>
          ) : (
            <TableScroll label={tn("caption")}>
              <table className="table-dense rp-table" aria-label={tn("caption")}>
                <caption className="rp-caption">{tn("caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{tn("family")}</th>
                    <th scope="col">{tn("detector")}</th>
                    <th scope="col" className="num">
                      {tn("judged")}
                    </th>
                    <th scope="col" className="num">
                      {tn("noise")}
                    </th>
                    <th scope="col" className="num">
                      {tn("ratio")}
                    </th>
                    <th scope="col">{tn("state")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const unmeasured = coverage.get(r.family) === "INSUFFICIENT_DATA";
                    const lower = r.state === "PARTIAL";
                    const count = (n: number) =>
                      unmeasured ? "—" : bound(lower, formatCount(n, locale));
                    return (
                      <tr key={`${r.family}:${r.anomaly_type}`} data-state={r.state}>
                        <td>{t(`family.${r.family}`)}</td>
                        <td>{detectorLabel(say, r.anomaly_type)}</td>
                        <td className="num">{count(r.judged)}</td>
                        <td className="num">{count(r.noise)}</td>
                        <td className="num">{r.ratio === null ? "—" : formatShare(r.ratio, locale)}</td>
                        <td className={r.state === "OK" ? "rp-basis muted" : "rp-basis"}>
                          {r.state === "INSUFFICIENT_DATA"
                            ? tn("insufficient")
                            : r.state === "PARTIAL"
                              ? tn("partial")
                              : t("state.ok")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}
          <CoverageNotes coverage={block.coverage} />
        </>
      )}
    </Frame>
  );
}
