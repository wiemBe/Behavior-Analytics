import Link from "next/link";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { CUSTOM_RULE_LATENCY, customRuleAccessFrom, type CustomRuleAccess } from "@/components/customrules/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type CustomRuleCatalog } from "@/lib/api";

export default async function NewCustomRulePage() {
  let catalog: CustomRuleCatalog | null = null;
  let access: CustomRuleAccess = "unknown";
  let forbidden = false;
  let failed = false;
  try {
    [catalog, access] = await Promise.all([
      api.customRuleCatalog(),
      api.caller().then(customRuleAccessFrom).catch(() => "unknown" as const),
    ]);
  } catch (error) {
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
  }

  return (
    <>
      <PageHeader title="New custom rule" />
      {forbidden ? (
        <div className="notice" role="alert">
          <p><strong>The custom-rule catalog is forbidden for this account.</strong></p>
          <p>Reading it needs <code>read:custom_rules</code>. Ask an admin to grant a role that includes it.</p>
        </div>
      ) : failed || catalog === null ? (
        <div className="notice" role="alert">
          <p><strong>The rule editor could not be loaded.</strong></p>
          <p>The catalog of metrics and bounds is unavailable. Check the backend, then reload the page.</p>
        </div>
      ) : access === "writer" ? (
        <>
          <p className="cr-latency">{CUSTOM_RULE_LATENCY}</p>
          <CustomRuleEditor catalog={catalog} mode="create" />
        </>
      ) : (
        <>
          <CustomRuleWriteNotice unknown={access === "unknown"} />
          <p className="field-hint"><Link href="/custom-rules">Back to custom rules</Link></p>
        </>
      )}
    </>
  );
}
