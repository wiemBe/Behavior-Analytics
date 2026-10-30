import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { RuleActions } from "@/components/customrules/RuleActions";
import { RuleStatus } from "@/components/customrules/RuleStatus";
import { StoredRulePreview } from "@/components/customrules/StoredRulePreview";
import {
  conditionText,
  customRuleAccessFrom,
  formatValue,
  statusLabel,
  statusTone,
  subjectKindLabel,
  type CustomRuleAccess,
} from "@/components/customrules/authoring";
import { resolveRuleSubjects, type RuleSubject } from "@/components/customrules/subjects";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  api,
  rateLimitedText,
  sevTone,
  type CustomRule,
  type CustomRuleBreach,
  type CustomRuleCatalog,
  type CustomRulePreview,
} from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { enumLabel } from "@/i18n/enumLabels";
import { withRouteMessages } from "@/i18n/RouteMessages";


function measuredValue(
  value: number | null,
  measurement: string | null,
  unit: string,
  locale: Locale,
  t: TextTranslator,
): string {
  if (value === null || measurement === null) return "—";
  const rendered = formatValue(value, unit, locale);
  return measurement === "LOWER_BOUND"
    ? t("customRules.detail.atLeast", { value: rendered })
    : rendered;
}

export async function generateMetadata() {
  const t = await getTranslations("customRules.meta");
  return { title: t("detail") };
}

async function CustomRuleDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const t = await getTranslations("customRules.detail");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  let rule: CustomRule | null = null;
  let catalog: CustomRuleCatalog | null = null;
  let preview: CustomRulePreview | null = null;
  let breaches: CustomRuleBreach[] = [];
  let access: CustomRuleAccess = "unknown";
  let limited: string | null = null;
  let forbidden = false;
  let failed = false;
  try {
    [rule, catalog, preview, breaches, access] = await Promise.all([
      api.customRule(id),
      api.customRuleCatalog(),
      api.customRulePreview(id, 24),
      api.customRuleBreaches(id),
      api.caller().then(customRuleAccessFrom).catch(() => "unknown" as const),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) {
      limited = rateLimitedText(error, say);
    } else {
      forbidden = error instanceof ApiError && error.status === 403;
      failed = !forbidden;
    }
  }

  if (limited !== null) {
    return (
      <>
        <PageHeader guide={guideFor("/custom-rules/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p><strong>{t("failedTitle", { id })}</strong></p>
          <p>{limited}</p>
        </div>
      </>
    );
  }
  if (forbidden) {
    return (
      <>
        <PageHeader guide={guideFor("/custom-rules/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p><strong>{t("forbiddenTitle")}</strong></p>
          <p>{t("forbiddenHelp")}</p>
        </div>
      </>
    );
  }
  if (failed || rule === null || catalog === null || preview === null) {
    return (
      <>
        <PageHeader guide={guideFor("/custom-rules/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p><strong>{t("failedTitle", { id })}</strong></p>
          <p>{t("failedHelp")}</p>
        </div>
      </>
    );
  }

  const subject: RuleSubject = (await resolveRuleSubjects([rule], say)).get(rule.id) ?? {
    label: t("unknownSubject"),
    href: "/anomalies",
    retiredAt: null,
  };
  const unit = rule.threshold_unit ?? preview.unit;
  const active = rule.active_breach;

  return (
    <>
      <PageHeader
        guide={guideFor("/custom-rules/[id]")}
        title={rule.name}
        meta={
          <>
            <span className={`pill ${sevTone(rule.severity)}`}>{enumLabel(say, "severityPill", rule.severity)}</span>
            <span className={`pill ${rule.enabled ? "ok" : ""}`}>{rule.enabled ? t("enabled") : t("disabled")}</span>
            <span className="pill">{rule.alerting_mode === "shadow" ? t("shadow") : t("alerting")}</span>
          </>
        }
        actions={access === "writer" ? <RuleActions rule={rule} /> : null}
      />

      <section className="cr-rule-identity" aria-labelledby="state-title">
        <h2 id="state-title">{t("currentState")}</h2>
        <RuleStatus status={rule.last_status} reason={rule.last_status_reason} reasonCode={rule.last_status_reason_code} reasonParams={rule.last_status_reason_params} />
        <dl className="cr-definition">
          <div><dt>{t("subject")}</dt><dd><span>{subjectKindLabel(rule.subject_kind, say)} · </span><Link href={subject.href}>{subject.label}</Link></dd></div>
          <div><dt>{t("condition")}</dt><dd>{conditionText(rule, locale, say)}</dd></div>
          <div><dt>{t("lastEvaluated")}</dt><dd>{rule.last_evaluated_at ? <UtcTime value={rule.last_evaluated_at} /> : t("never")}</dd></div>
        </dl>
        {rule.last_status === "SUBJECT_RETIRED" && (
          <div className="banner">
            {subject.retiredAt ? (
              <>{t("retiredAtBefore")} <UtcTime value={subject.retiredAt} />. {t("retiredAtAfter")}</>
            ) : (
              t("retired")
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="preview-heading">
        <h2 id="preview-heading">{t("readHeading")}</h2>
        <StoredRulePreview ruleId={rule.id} subjectLabel={subject.label} catalog={catalog} initial={preview} />
      </section>

      <section aria-labelledby="active-heading">
        <h2 id="active-heading">{t("activeHeading")}</h2>
        {active === null ? (
          <div className="notice"><p>{t("noActive")}</p></div>
        ) : (
          <div className="panel cr-active-breach">
            <dl className="cr-definition">
              <div><dt>{t("state")}</dt><dd><span className={`pill ${statusTone(active.state)}`}>{statusLabel(active.state, say)}</span></dd></div>
              <div><dt>{t("worst")}</dt><dd className="num">{measuredValue(active.worst_value, active.worst_measurement, unit, locale, say)}</dd></div>
              <div><dt>{t("last")}</dt><dd className="num">{measuredValue(active.last_value, active.last_measurement, unit, locale, say)}</dd></div>
              <div><dt>{t("unmeasured")}</dt><dd className="num">{t("buckets", { count: active.unmeasured_buckets })}</dd></div>
              <div><dt>{t("opened")}</dt><dd><UtcTime value={active.opened_at} /></dd></div>
              <div><dt>{t("lastBucket")}</dt><dd><UtcTime value={active.last_bucket_start} /></dd></div>
            </dl>
            {rule.alerting_mode === "alerting" ? (
              <p>{t("alertOpenBefore")} <Link href="/alerts">{t("alertsLink")}</Link>{t("alertOpenAfter")}</p>
            ) : (
              <p>{t("shadowBreach")}</p>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="history-heading">
        <h2 id="history-heading">{t("historyHeading")}</h2>
        {breaches.length === 0 ? (
          <div className="notice"><p>{t("historyEmpty")}</p></div>
        ) : (
          <TableScroll label={t("historyLabel")}>
            <table aria-label={t("historyLabel")}>
              <thead><tr><th scope="col">{t("state")}</th><th scope="col">{t("openedUtc")}</th><th scope="col">{t("resolved")}</th><th scope="col" className="num">{t("worstUnit", { unit })}</th><th scope="col" className="num">{t("lastUnit", { unit })}</th><th scope="col" className="num">{t("unmeasured")}</th></tr></thead>
              <tbody>
                {breaches.map((breach) => (
                  <tr key={breach.id}>
                    <td><span className={`pill ${statusTone(breach.state)}`}>{statusLabel(breach.state, say)}</span></td>
                    <td><UtcTime value={breach.opened_at} /></td>
                    <td>{breach.resolved_at ? <UtcTime value={breach.resolved_at} /> : "—"}</td>
                    <td className="num">{measuredValue(breach.worst_value, breach.worst_measurement, unit, locale, say)}</td>
                    <td className="num">{measuredValue(breach.last_value, breach.last_measurement, unit, locale, say)}</td>
                    <td className="num">{t("buckets", { count: breach.unmeasured_buckets })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>

      <section aria-labelledby="edit-heading">
        <h2 id="edit-heading">{t("definition")}</h2>
        {access === "writer" ? (
          <details className="cr-edit-disclosure">
            <summary>{t("edit")}</summary>
            <CustomRuleEditor catalog={catalog} mode="edit" rule={rule} initialSubjectLabel={subject.label} />
          </details>
        ) : (
          <CustomRuleWriteNotice unknown={access === "unknown"} />
        )}
      </section>
    </>
  );
}

export default withRouteMessages("/custom-rules/[id]", CustomRuleDetailPage);
