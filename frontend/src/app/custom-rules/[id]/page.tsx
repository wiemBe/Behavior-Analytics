import Link from "next/link";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { RuleActions } from "@/components/customrules/RuleActions";
import { RuleStatus } from "@/components/customrules/RuleStatus";
import { StoredRulePreview } from "@/components/customrules/StoredRulePreview";
import {
  SUBJECT_KIND_LABELS,
  conditionText,
  customRuleAccessFrom,
  formatValue,
  statusLabel,
  statusTone,
  type CustomRuleAccess,
} from "@/components/customrules/authoring";
import { resolveRuleSubjects, type RuleSubject } from "@/components/customrules/subjects";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  api,
  sevTone,
  type CustomRule,
  type CustomRuleBreach,
  type CustomRuleCatalog,
  type CustomRulePreview,
} from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";


function measuredValue(value: number | null, measurement: string | null, unit: string): string {
  if (value === null || measurement === null) return "—";
  const rendered = formatValue(value, unit);
  return measurement === "LOWER_BOUND" ? `at least ${rendered}` : rendered;
}

export default async function CustomRuleDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let rule: CustomRule | null = null;
  let catalog: CustomRuleCatalog | null = null;
  let preview: CustomRulePreview | null = null;
  let breaches: CustomRuleBreach[] = [];
  let access: CustomRuleAccess = "unknown";
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
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
  }

  if (forbidden) {
    return (
      <>
        <PageHeader title="Custom rule" />
        <div className="notice" role="alert">
          <p><strong>This custom rule is forbidden for this account.</strong></p>
          <p>Reading it needs <code>read:custom_rules</code>. Ask an admin to grant a role that includes it.</p>
        </div>
      </>
    );
  }
  if (failed || rule === null || catalog === null || preview === null) {
    return (
      <>
        <PageHeader title="Custom rule" />
        <div className="notice" role="alert">
          <p><strong>Custom rule {id} could not be loaded.</strong></p>
          <p>It may have been deleted, or the backend may be unreachable. Reload the page to try again.</p>
        </div>
      </>
    );
  }

  const subject: RuleSubject = (await resolveRuleSubjects([rule])).get(rule.id) ?? {
    label: "Unknown subject",
    href: "/anomalies",
    retiredAt: null,
  };
  const unit = rule.threshold_unit ?? preview.unit;
  const active = rule.active_breach;

  return (
    <>
      <PageHeader
        title={rule.name}
        meta={
          <>
            <span className={`pill ${sevTone(rule.severity)}`}>{rule.severity}</span>
            <span className={`pill ${rule.enabled ? "ok" : ""}`}>{rule.enabled ? "Enabled" : "Disabled"}</span>
            <span className="pill">{rule.alerting_mode === "shadow" ? "Shadow mode" : "Alerting mode"}</span>
          </>
        }
        actions={access === "writer" ? <RuleActions rule={rule} /> : null}
      />

      <section className="cr-rule-identity" aria-labelledby="state-title">
        <h2 id="state-title">Current state</h2>
        <RuleStatus status={rule.last_status} reason={rule.last_status_reason} />
        <dl className="cr-definition">
          <div><dt>Subject</dt><dd><span>{SUBJECT_KIND_LABELS[rule.subject_kind]} · </span><Link href={subject.href}>{subject.label}</Link></dd></div>
          <div><dt>Condition</dt><dd>{conditionText(rule)}</dd></div>
          <div><dt>Last evaluated</dt><dd>{rule.last_evaluated_at ? <UtcTime value={rule.last_evaluated_at} /> : "Never evaluated"}</dd></div>
        </dl>
        {rule.last_status === "SUBJECT_RETIRED" && (
          <div className="banner">
            {subject.retiredAt ? (
              <>This subject stopped being tracked at <UtcTime value={subject.retiredAt} />. The rule draws no verdict from later buckets.</>
            ) : (
              "This subject stopped being tracked. The rule draws no verdict from later buckets."
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="preview-heading">
        <h2 id="preview-heading">What the rule read</h2>
        <StoredRulePreview ruleId={rule.id} subjectLabel={subject.label} catalog={catalog} initial={preview} />
      </section>

      <section aria-labelledby="active-heading">
        <h2 id="active-heading">Active breach</h2>
        {active === null ? (
          <div className="notice"><p>No active breach is recorded for this rule.</p></div>
        ) : (
          <div className="panel cr-active-breach">
            <dl className="cr-definition">
              <div><dt>State</dt><dd><span className={`pill ${statusTone(active.state)}`}>{statusLabel(active.state)}</span></dd></div>
              <div><dt>Worst reading</dt><dd className="num">{measuredValue(active.worst_value, active.worst_measurement, unit)}</dd></div>
              <div><dt>Last reading</dt><dd className="num">{measuredValue(active.last_value, active.last_measurement, unit)}</dd></div>
              <div><dt>Unmeasured</dt><dd className="num">{active.unmeasured_buckets} buckets</dd></div>
              <div><dt>Opened</dt><dd><UtcTime value={active.opened_at} /></dd></div>
              <div><dt>Last bucket</dt><dd><UtcTime value={active.last_bucket_start} /></dd></div>
            </dl>
            {rule.alerting_mode === "alerting" ? (
              <p>An alert is open in <Link href="/alerts">Alerts</Link>.</p>
            ) : (
              <p>This breach was recorded in shadow mode. Nothing paged.</p>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="history-heading">
        <h2 id="history-heading">Breach history</h2>
        {breaches.length === 0 ? (
          <div className="notice"><p>No breach has opened for this rule.</p></div>
        ) : (
          <TableScroll label="Breach history">
            <table aria-label="Breach history">
              <thead><tr><th scope="col">State</th><th scope="col">Opened (UTC)</th><th scope="col">Resolved (UTC)</th><th scope="col" className="num">Worst ({unit})</th><th scope="col" className="num">Last ({unit})</th><th scope="col" className="num">Unmeasured</th></tr></thead>
              <tbody>
                {breaches.map((breach) => (
                  <tr key={breach.id}>
                    <td><span className={`pill ${statusTone(breach.state)}`}>{statusLabel(breach.state)}</span></td>
                    <td><UtcTime value={breach.opened_at} /></td>
                    <td>{breach.resolved_at ? <UtcTime value={breach.resolved_at} /> : "—"}</td>
                    <td className="num">{measuredValue(breach.worst_value, breach.worst_measurement, unit)}</td>
                    <td className="num">{measuredValue(breach.last_value, breach.last_measurement, unit)}</td>
                    <td className="num">{breach.unmeasured_buckets} buckets</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>

      <section aria-labelledby="edit-heading">
        <h2 id="edit-heading">Rule definition</h2>
        {access === "writer" ? (
          <details className="cr-edit-disclosure">
            <summary>Edit rule</summary>
            <CustomRuleEditor catalog={catalog} mode="edit" rule={rule} initialSubjectLabel={subject.label} />
          </details>
        ) : (
          <CustomRuleWriteNotice unknown={access === "unknown"} />
        )}
      </section>
    </>
  );
}
