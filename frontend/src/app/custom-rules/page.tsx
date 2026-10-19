import Link from "next/link";

import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { RuleStatus } from "@/components/customrules/RuleStatus";
import {
  conditionText,
  customRuleLatency,
  customRuleAccessFrom,
  type CustomRuleAccess,
  SUBJECT_KIND_LABELS,
} from "@/components/customrules/authoring";
import { resolveRuleSubjects } from "@/components/customrules/subjects";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, sevTone, type CallerInfo, type CustomRule, type CustomRuleCatalog } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

export default async function CustomRulesPage() {
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
  const subjects = failed || forbidden ? new Map() : await resolveRuleSubjects(rules);

  return (
    <>
      <PageHeader
        title="Custom rules"
        actions={access === "writer" && !forbidden ? <Link className="action secondary" href="/custom-rules/new">New rule</Link> : null}
      />

      {forbidden ? (
        <div className="notice" role="alert">
          <p><strong>Custom rules are forbidden for this account.</strong></p>
          <p>Reading this route needs <code>read:custom_rules</code>. Ask an admin to grant a role that includes it.</p>
        </div>
      ) : failed || catalog === null ? (
        <div className="notice" role="alert">
          <p><strong>Custom rules could not be loaded.</strong></p>
          <p>Check that the backend is reachable, then reload the page. No rule was changed.</p>
        </div>
      ) : (
        <>
          <div className="cr-page-notices">
            {!catalog.evaluation_enabled && (
              <div className="banner warn" role="status">
                Rule evaluation is off because <code>CUSTOM_RULES_ENABLED</code> is false. Enabled rules are not being judged.
              </div>
            )}
            {catalog.alerting_mode === "shadow" && (
              <div className="banner" role="status">
                Breaches are recorded and nothing pages until <code>CUSTOM_RULES_ALERTING_ENABLED</code> is on.
              </div>
            )}
            <CadenceLatency className="cr-latency" parts={customRuleLatency()} canLink={canLink} />
          </div>

          {rules.length === 0 ? (
            <div className="notice">
              <p><strong>No custom rules exist yet.</strong></p>
              <p>A custom rule judges one collected metric for one subject over consecutive 5-minute buckets.</p>
              {access === "writer" ? (
                <p><Link href="/custom-rules/new">Create the first rule</Link> and preview it over stored data before enabling it.</p>
              ) : (
                <p>Creating one needs <code>custom_rule:write</code>. An admin or the <code>detection_engineer</code> role can grant it.</p>
              )}
            </div>
          ) : (
            <TableScroll label="Custom rules">
              <table className="table-dense cr-rule-table" aria-label="Custom rules">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Status and reason</th>
                    <th scope="col">Subject</th>
                    <th scope="col">Condition</th>
                    <th scope="col">Severity</th>
                    <th scope="col">Enabled</th>
                    <th scope="col">Last evaluated (UTC)</th>
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
                          <span className="cr-cell-kicker">{SUBJECT_KIND_LABELS[rule.subject_kind]}</span>
                          {subject ? <Link href={subject.href}>{subject.label}</Link> : "—"}
                        </td>
                        <td>{conditionText(rule)}</td>
                        <td><span className={`pill ${sevTone(rule.severity)}`}>{rule.severity}</span></td>
                        <td>{rule.enabled ? "Yes" : "No"}</td>
                        <td className="num cell-when">
                          {rule.last_evaluated_at ? <time dateTime={rule.last_evaluated_at}>{formatDateTime(rule.last_evaluated_at)}</time> : "Never evaluated"}
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
