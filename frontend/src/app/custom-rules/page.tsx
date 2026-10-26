import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

import { canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { CustomRuleLatency } from "@/components/customrules/CustomRuleLatency";
import { RuleStatus } from "@/components/customrules/RuleStatus";
import {
  conditionText,
  customRuleAccessFrom,
  type CustomRuleAccess,
  subjectKindLabel,
} from "@/components/customrules/authoring";
import { resolveRuleSubjects } from "@/components/customrules/subjects";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, sevTone, type CallerInfo, type CustomRule, type CustomRuleCatalog } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import { enumLabel } from "@/i18n/enumLabels";

const EVALUATION_SETTING = "CUSTOM_RULES_ENABLED";
const ALERTING_SETTING = "CUSTOM_RULES_ALERTING_ENABLED";
const WRITE_PERMISSION = "custom_rule:write";
const ENGINEER_ROLE = "detection_engineer";

export async function generateMetadata() {
  const t = await getTranslations("customRules.meta");
  return { title: t("list") };
}


export default async function CustomRulesPage() {
  const t = await getTranslations("customRules");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  let rules: CustomRule[] = [];
  let catalog: CustomRuleCatalog | null = null;
  let access: CustomRuleAccess = "unknown";
  let canLink = false;
  let failed = false;
  let forbidden = false;
  try {
    let caller: CallerInfo | null = null;
    [rules, catalog, caller] = await Promise.all([
      api.customRules(),
      api.customRuleCatalog(),
      api.caller().catch(() => null),
    ]);
    access = caller ? customRuleAccessFrom(caller) : "unknown";
    canLink = canReadSearchCadence(caller);
  } catch (error) {
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
    try {
      access = customRuleAccessFrom(await api.caller());
    } catch {
      access = "unknown";
    }
  }
  const subjects = failed || forbidden ? new Map() : await resolveRuleSubjects(rules, say);

  return (
    <>
      <PageHeader
        title={t("title")}
        actions={access === "writer" && !forbidden ? <Link className="action secondary" href="/custom-rules/new">{t("newRule")}</Link> : null}
      />

      {forbidden ? (
        <div className="notice" role="alert">
          <p><strong>{t("forbiddenTitle")}</strong></p>
          <p>{t("forbiddenHelp")}</p>
        </div>
      ) : failed || catalog === null ? (
        <div className="notice" role="alert">
          <p><strong>{t("failedTitle")}</strong></p>
          <p>{t("failedHelp")}</p>
        </div>
      ) : (
        <>
          <div className="cr-page-notices">
            {!catalog.evaluation_enabled && (
              <div className="banner warn" role="status">
                {t("evaluationOffBefore")}<code>{EVALUATION_SETTING}</code>{t("evaluationOffAfter")}.
              </div>
            )}
            {catalog.alerting_mode === "shadow" && (
              <div className="banner" role="status">
                {t("shadowBefore")}<code>{ALERTING_SETTING}</code>{t("shadowAfter")}.
              </div>
            )}
            <CustomRuleLatency className="cr-latency" catalog={catalog} canLink={canLink} />
          </div>

          {rules.length === 0 ? (
            <div className="notice">
              <p><strong>{t("emptyTitle")}</strong></p>
              <p>{t("emptyHelp")}</p>
              {access === "writer" ? (
                <p><Link href="/custom-rules/new">{t("emptyWriter")}</Link> {t("emptyWriterAfter")}</p>
              ) : (
                <p>
                  {t("emptyReaderBefore")}<code>{WRITE_PERMISSION}</code>{t("emptyReaderMiddle")}
                  <code>{ENGINEER_ROLE}</code>{t("emptyReaderAfter")}.
                </p>
              )}
            </div>
          ) : (
            <TableScroll label={t("table.label")}>
              <table className="table-dense cr-rule-table" aria-label={t("table.label")}>
                <thead>
                  <tr>
                    <th scope="col">{t("table.name")}</th>
                    <th scope="col">{t("table.status")}</th>
                    <th scope="col">{t("table.subject")}</th>
                    <th scope="col">{t("table.condition")}</th>
                    <th scope="col">{t("table.severity")}</th>
                    <th scope="col">{t("table.enabled")}</th>
                    <th scope="col">{t("table.lastEvaluated")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((rule) => {
                    const subject = subjects.get(rule.id);
                    return (
                      <tr key={rule.id}>
                        <td className="cell-name"><Link href={`/custom-rules/${rule.id}`}>{rule.name}</Link></td>
                        <td><RuleStatus status={rule.last_status} reason={rule.last_status_reason} /></td>
                        <td className="cell-name">
                          <span className="cr-cell-kicker">{subjectKindLabel(rule.subject_kind, say)}</span>
                          {subject ? <Link href={subject.href}>{subject.label}</Link> : "—"}
                        </td>
                        <td>{conditionText(rule, locale, say)}</td>
                        <td><span className={`pill ${sevTone(rule.severity)}`}>{enumLabel(say, "severityPill", rule.severity)}</span></td>
                        <td>{rule.enabled ? t("table.yes") : t("table.no")}</td>
                        <td className="num cell-when">
                          {rule.last_evaluated_at ? <UtcTime value={rule.last_evaluated_at} /> : t("table.never")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}
        </>
      )}
    </>
  );
}
