import Link from "next/link";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { customRuleAccessFrom, customRuleLatency, type CustomRuleAccess } from "@/components/customrules/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type CallerInfo, type CustomRuleCatalog } from "@/lib/api";

export default async function NewCustomRulePage() {
  let catalog: CustomRuleCatalog | null = null;
  let access: CustomRuleAccess = "unknown";
  let canLink = false;
  let forbidden = false;
  let failed = false;
  try {
    let caller: CallerInfo | null = null;
    [catalog, caller] = await Promise.all([
      api.customRuleCatalog(),
      api.caller().catch(() => null),
    ]);
    access = caller ? customRuleAccessFrom(caller) : "unknown";
    canLink = canReadSearchCadence(caller);
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
          <CadenceLatency className="cr-latency" parts={customRuleLatency()} canLink={canLink} />
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
