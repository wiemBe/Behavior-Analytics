import { getLocale, getTranslations } from "next-intl/server";

import {
  api,
  type CoverageSummary,
  type DataSourceCoverageView,
  type Page,
  type RuleCoverageView,
  type TechniqueCoverage,
} from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { asTextTranslator } from "@/i18n/textTranslator";
import { kindLabel, mappingSourceLabel, statusLabel } from "@/lib/coverageText";
import { formatShare, type FormatLocale } from "@/lib/format";
import { plainCount } from "@/lib/fleetText";
import { healthMeaning, healthTone } from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";


import "./coverage.css";

const PAGE_SIZE = 50;

// The statuses we always show a counter for, so a zero is visibly zero rather
// than silently absent.
const STATUSES = ["COVERED", "PARTIAL", "DEGRADED", "MISSING", "NOT_EVALUATED"];

export default async function CoveragePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const t = await getTranslations("coverage");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);

  let summary: CoverageSummary | null = null;
  let page: Page<TechniqueCoverage> | null = null;
  let byRule: RuleCoverageView[] = [];
  let byDataSource: DataSourceCoverageView[] = [];
  let error: string | null = null;

  try {
    [summary, page, byRule, byDataSource] = await Promise.all([
      api.coverageSummary(),
      api.coverageTechniques({ limit: PAGE_SIZE, offset }),
      api.coverageByRule(),
      api.coverageByDataSource(),
    ]);
  } catch (err) {
    error = loadErrorMessage(err, "detection coverage", "read:coverage");
  }

  if (error) {
    return (
      <>
        <PageHeader title={t("title")} />
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      </>
    );
  }

  const items = page?.items ?? [];
  const cardCount =
    STATUSES.length + Object.keys(summary?.by_status ?? {}).filter((s) => !STATUSES.includes(s)).length;
  const nothingMapped = (summary?.total_techniques ?? 0) === 0;

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
      />

      {nothingMapped ? (
        // The honest empty state. Showing 0% covered here would be a claim we
        // cannot support; showing 100% would be worse.
        <div className="notice">
          <strong>{t("notEvaluatedHeading")}</strong>
          <p>{t("notEvaluatedBody")}</p>
        </div>
      ) : (
        <>
          <div className={`cards coverage-cards coverage-cards-${Math.ceil(cardCount / 2)}`}>
            {STATUSES.map((s) => (
              <StatCard
                key={s}
                label={statusLabel(say, s)}
                value={plainCount(summary?.by_status?.[s] ?? 0, locale)}
                tone={healthTone(s) || undefined}
              />
            ))}
            {Object.keys(summary?.by_status ?? {})
              .filter((s) => !STATUSES.includes(s))
              .map((s) => (
                <StatCard
                  key={s}
                  label={statusLabel(say, s)}
                  value={plainCount(summary?.by_status?.[s] ?? 0, locale)}
                  tone={healthTone(s) || undefined}
                />
              ))}
          </div>

          <p className="muted">
            {t.rich("summary", {
              total: summary ? plainCount(summary.total_techniques, locale) : "",
              covered: formatShare(summary?.covered_ratio, locale),
              explicit: plainCount(summary?.mapping_provenance.explicit ?? 0, locale),
              inferred: plainCount(summary?.mapping_provenance.inferred ?? 0, locale),
              time: () => <UtcTime value={summary?.generated_at} />,
            })}
          </p>

          {items.length === 0 ? (
            <div className="notice">{t("noTechniques")}</div>
          ) : (
            <>
              <TableScroll label={t("techniques.label")}>
              <table className="coverage-table" aria-label={t("techniques.label")}>
                <thead>
                  <tr>
                    <th>{t("techniques.technique")}</th>
                    <th>{t("techniques.name")}</th>
                    <th>{t("techniques.tactic")}</th>
                    <th>{t("techniques.status")}</th>
                    <th className="num">{t("techniques.rules")}</th>
                    <th className="num">{t("techniques.score")}</th>
                    <th>{t("techniques.evidence")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((tq) => (
                    <tr key={tq.technique_id}>
                      <td>{tq.technique_id}</td>
                      <td>{tq.technique_name ?? "—"}</td>
                      <td>{tq.tactic ?? "—"}</td>
                      <td>
                        <span className={`pill ${healthTone(tq.status)}`}>
                          {statusLabel(say, tq.status)}
                        </span>
                      </td>
                      <td className="num">{plainCount(tq.mapped_rule_count, locale)}</td>
                      <td className="num">
                        {formatShare(tq.coverage_score, locale)}
                      </td>
                      <td className="muted">{tq.reason ?? healthMeaning(tq.status, say)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </TableScroll>

              <Pagination
                total={page?.total ?? 0}
                limit={PAGE_SIZE}
                offset={offset}
                basePath="/coverage"
              />
            </>
          )}
        </>
      )}

      <h3>{t("byRule.heading")}</h3>
      {byRule.length === 0 ? (
        <div className="notice">{t("byRule.empty")}</div>
      ) : (
        <TableScroll label={t("byRule.label")}>
        <table className="coverage-table" aria-label={t("byRule.label")}>
          <thead><tr><th>{t("byRule.rule")}</th><th>{t("byRule.enabled")}</th><th>{t("byRule.health")}</th><th>{t("byRule.techniques")}</th><th>{t("byRule.evidence")}</th></tr></thead>
          <tbody>
            {byRule.map((r) => (
              <tr key={r.rule_id}>
                <td>{r.name}</td><td>{r.enabled ? t("byRule.yes") : t("byRule.no")}</td>
                <td><span className={`pill ${healthTone(r.health_status)}`}>{statusLabel(say, r.health_status)}</span></td>
                <td>{r.techniques.map((tq) => tq.technique_id).join(", ")}</td>
                <td className="muted">{r.techniques.map((tq) => `${mappingSourceLabel(say, tq.source)} ${formatShare(tq.confidence, locale)}`).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}

      <h3>{t("byDataSource.heading")}</h3>
      {byDataSource.length === 0 ? (
        <div className="notice">{t("byDataSource.empty")}</div>
      ) : (
        <TableScroll label={t("byDataSource.label")}>
        <table className="coverage-table" aria-label={t("byDataSource.label")}>
          <thead><tr><th>{t("byDataSource.dataSource")}</th><th>{t("byDataSource.kind")}</th><th>{t("byDataSource.rules")}</th><th>{t("byDataSource.techniques")}</th><th>{t("byDataSource.evidence")}</th></tr></thead>
          <tbody>
            {byDataSource.map((d) => (
              <tr key={`${d.kind}:${d.target_ref}`}>
                <td>{d.target_name ?? d.target_ref}</td><td>{kindLabel(say, d.kind)}</td>
                <td>{d.rules.map((r) => r.name).join(", ")}</td>
                <td>{d.techniques.join(", ") || "—"}</td>
                <td className="muted">{d.rules.map((r) => `${mappingSourceLabel(say, r.dependency_source)} ${formatShare(r.dependency_confidence, locale)}`).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </>
  );
}
