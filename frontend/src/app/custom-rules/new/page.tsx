import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { CustomRuleLatency } from "@/components/customrules/CustomRuleLatency";
import { customRuleAccessFrom, type CustomRuleAccess } from "@/components/customrules/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type CallerInfo, type CustomRuleCatalog } from "@/lib/api";

export async function generateMetadata() {
  const t = await getTranslations("customRules.meta");
  return { title: t("new") };
}

export default async function NewCustomRulePage() {
  const t = await getTranslations("customRules.new");
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
      <PageHeader title={t("title")} />
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
      ) : access === "writer" ? (
        <>
          <CustomRuleLatency className="cr-latency" catalog={catalog} canLink={canLink} />
          <CustomRuleEditor catalog={catalog} mode="create" />
        </>
      ) : (
        <>
          <CustomRuleWriteNotice unknown={access === "unknown"} />
          <p className="field-hint"><Link href="/custom-rules">{t("back")}</Link></p>
        </>
      )}
    </>
  );
}
