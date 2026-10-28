import {
  api,
  type CapacitySample,
  type RuleDetail,
  type RuleHealthSnapshot,
  ruleStatisticsApi,
} from "@/lib/api";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { CapacityHistory } from "@/components/rules/CapacityHistory";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatShare } from "@/lib/format";
import { healthMeaning, healthTone, isUnestablished } from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";
import { formatCount } from "@/lib/format";
import { dependencyWord } from "@/components/rules/dependencyWord";

import "@/components/ui/controls.css";

import "../rules.css";

export async function generateMetadata() {
  const t = await getTranslations("rules.meta");
  return { title: t("detail") };
}

export default async function RuleDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("rules.detail");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;

  let rule: RuleDetail | null = null;
  let history: RuleHealthSnapshot[] | null = [];
  let capacityHistory: CapacitySample[] | null = [];
  let missing = false;
  let error = false;

  try {
    [rule, history, capacityHistory] = await Promise.all([
      api.rule(id),
      api.ruleHealthHistory(id).catch(() => null),
      ruleStatisticsApi.capacityHistory(id).catch(() => null),
    ]);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error || !rule) {
    return (
      <>
        <PageHeader guide={guideFor("/rules/[id]")} title={t("title")} />
        <div className="notice" role={missing ? "status" : "alert"}>
          <p>
            {missing
              ? t("missing")
              : t("failed")}
          </p>
        </div>
        <p>
          <Link href="/rules">{t("back")}</Link>
        </p>
      </>
    );
  }

  const r = rule;
  // The reason stored with the verdict on the card above, not the newest history row: the two
  // can differ, and a reason must belong to the verdict it sits beside.
  const reason = r.health_reason?.trim() || null;
  const buildingBlocks = r.dependencies.filter((d) => d.kind === "BUILDING_BLOCK");
  const telemetry = r.dependencies.filter((d) => d.kind !== "BUILDING_BLOCK");
  const dependencyLabel = (group: "mappingSources" | "dependencyKinds", value: string) =>
    dependencyWord(say, group, value);

  return (
    <>
      <p className="muted">
        <Link href="/rules">{t("crumb")}</Link>
      </p>
      <PageHeader
        guide={guideFor("/rules/[id]")}
        title={r.name}
        description={t("description", {
          id: r.qradar_id,
          buildingBlock: r.is_building_block ? t("buildingBlockSuffix") : "",
        })}
      />

      <div className="cards rule-cards rule-cards-2">
        <div className="card">
          <div className="k">{t("health")}</div>
          <div className={`v ${healthTone(r.health_status)}`}>
            {r.health_status ? enumLabel(say, "ruleHealth", r.health_status) : "—"}
          </div>
        </div>
        <div className="card">
          <div className="k">{t("enabled")}</div>
          <div className={`v ${r.enabled ? "ok" : "warn"}`}>{r.enabled ? t("yes") : t("no")}</div>
        </div>
        <div className="card">
          <div className="k">{t("offenseContributions")}</div>
          <div className="v">{formatCount(r.offense_contribution_count, locale)}</div>
        </div>
        <div className="card">
          <div className="k">{t("eventContributions")}</div>
          <div className="v">{formatCount(r.event_contribution_count, locale)}</div>
        </div>
      </div>

      <h3>{t("healthEvidence")}</h3>
      {isUnestablished(r.health_status) ? (
        <div className="notice">
          <strong>{healthMeaning(r.health_status, say)}</strong>
          <p className="rule-health-reason">
            {reason ??
              t("unestablishedFallback")}
          </p>
        </div>
      ) : (
        <p className="rule-health-reason">{reason ?? healthMeaning(r.health_status, say)}</p>
      )}

      <div className="detail-grid">
        <div className="detail-block">
          <h4>{t("buildingBlocks", { count: formatCount(buildingBlocks.length, locale) })}</h4>
          {buildingBlocks.length === 0 ? (
            <p className="muted">{t("noBuildingBlocks")}</p>
          ) : (
            <ul>
              {buildingBlocks.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {t("dependency", {
                    name: d.target_name ?? d.target_ref,
                    source: dependencyLabel("mappingSources", d.source),
                    confidence: formatShare(d.confidence, locale),
                  })}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="detail-block">
          <h4>{t("telemetry", { count: formatCount(telemetry.length, locale) })}</h4>
          {telemetry.length === 0 ? (
            <p className="muted">{t("noTelemetry")}</p>
          ) : (
            <ul>
              {telemetry.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {t("telemetryDependency", {
                    name: d.target_name ?? d.target_ref,
                    kind: dependencyLabel("dependencyKinds", d.kind),
                    source: dependencyLabel("mappingSources", d.source),
                  })}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <h3>{t("metadata")}</h3>
      <TableScroll label={t("metadataLabel")}>
      <table className="kv-table" aria-label={t("metadataLabel")}>
        <tbody>
          <tr>
            <th>{t("ruleType")}</th>
            <td>{r.rule_type ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("origin")}</th>
            <td>{r.origin ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("owner")}</th>
            <td>{r.owner ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("buildingBlock")}</th>
            <td>{r.is_building_block ? t("yes") : t("no")}</td>
          </tr>
          <tr>
            <th>{t("lastFired")}</th>
            <td>
              {r.last_fired_at ? (
                <UtcTime value={r.last_fired_at} />
              ) : (
                <span className="muted">{t("notObserved")}</span>
              )}
            </td>
          </tr>
          <tr>
            <th>{t("healthEvaluated")}</th>
            <td>{<UtcTime value={r.health_evaluated_at} />}</td>
          </tr>
          <tr>
            <th>{t("mitre")}</th>
            <td>
              {r.mitre_techniques.length === 0 ? (
                <span className="muted">{t("noneMapped")}</span>
              ) : (
                <ul className="chips">
                  {r.mitre_techniques.map((t) => (
                    <li key={t} className="pill">
                      {t}
                    </li>
                  ))}
                </ul>
              )}
            </td>
          </tr>
        </tbody>
      </table>
      </TableScroll>

      <CapacityHistory samples={capacityHistory} />

      <h3>{t("history")}</h3>
      {history === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("historyFailedTitle")}</strong>
          </p>
          <p>
            {t("historyFailedHelp")}
          </p>
        </div>
      ) : history.length === 0 ? (
        <div className="notice">{t("historyEmpty")}</div>
      ) : (
        <TableScroll label={t("historyLabel")}>
        <table className="rules-history table-dense" aria-label={t("historyLabel")}>
          <thead>
            <tr>
              <th>{t("evaluated")}</th>
              <th>{t("status")}</th>
              <th className="num">{t("confidence")}</th>
              <th className="num">{t("triggers")}</th>
              <th className="num">{t("offenses")}</th>
              <th>{t("reason")}</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.evaluated_at}>
                <td className="nowrap">{<UtcTime value={h.evaluated_at} />}</td>
                <td><span className={`pill ${healthTone(h.status)}`}>{enumLabel(say, "ruleHealth", h.status)}</span></td>
                <td className="num">{formatShare(h.confidence, locale)}</td>
                <td className="num">{formatCount(h.trigger_count, locale)}</td>
                <td className="num">{formatCount(h.offense_contribution_count, locale)}</td>
                <td className="muted cell-reason">{h.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </>
  );
}
