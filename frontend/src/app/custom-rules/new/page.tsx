import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { CustomRuleEditor } from "@/components/customrules/CustomRuleEditor";
import { CustomRuleWriteNotice } from "@/components/customrules/CustomRuleWriteNotice";
import { canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { CustomRuleLatency } from "@/components/customrules/CustomRuleLatency";
import { customRuleAccessFrom, type CustomRuleAccess } from "@/components/customrules/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, api, rateLimitedText, type CallerInfo, type CustomRuleCatalog } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata() {
  const t = await getTranslations("customRules.meta");
  return { title: t("new") };
}

async function NewCustomRulePage() {
  const t = await getTranslations("customRules.new");
  const say = asTextTranslator(await getTranslations());
  let catalog: CustomRuleCatalog | null = null;
  let access: CustomRuleAccess = "unknown";
  let canLink = false;
  let limited: string | null = null;
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
    if (error instanceof ApiError && error.status === 429) {
      limited = rateLimitedText(error, say);
    } else {
      forbidden = error instanceof ApiError && error.status === 403;
      failed = !forbidden;
    }
  }

  return (
    <>
      <PageHeader guide={guideFor("/custom-rules/new")} title={t("title")} />
      {limited !== null ? (
        <div className="notice" role="alert">
          <p><strong>{t("failedTitle")}</strong></p>
          <p>{limited}</p>
        </div>
      ) : forbidden ? (
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

export default withRouteMessages("/custom-rules/new", NewCustomRulePage);
