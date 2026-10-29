// Which silent sources page someone, and which are only recorded.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  outagePolicyApi,
  type OutageFleetSummary,
  type OutagePolicyRow,
} from "@/lib/api";

import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { withRouteMessages } from "@/i18n/RouteMessages";

import { OutagePolicyManager } from "./OutagePolicyManager";
import { readError } from "./outage";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("outage.meta");
  return { title: t("title") };
}

async function OutagePolicyPage() {
  const t = await getTranslations("outage.page");
  const i18n = await getBehaviorI18n();
  let rows: OutagePolicyRow[] = [];
  let summary: OutageFleetSummary | null = null;
  let summaryNote: string | null = null;
  let failure: { forbidden: boolean; text: string } | null = null;

  const [policy, fleet] = await Promise.allSettled([
    outagePolicyApi.list(),
    outagePolicyApi.summary("24h"),
  ]);
  if (policy.status === "fulfilled") {
    rows = policy.value;
  } else {
    failure = readError(policy.reason, i18n);
  }
  if (fleet.status === "fulfilled") {
    summary = fleet.value;
  } else {
    summaryNote =
      fleet.reason instanceof ApiError && fleet.reason.status === 403
        ? t("summaryForbidden")
        : t("summaryFailed");
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/admin/outage-policy")}
        title={t("title")}
        description={t("description")}
      />

      {failure ? (
        <div className="notice" role="alert">
          <p>
            <strong>
              {failure.forbidden ? t("failureForbidden") : t("failureFailed")}
            </strong>
          </p>
          <p>{failure.text}</p>
        </div>
      ) : (
        <>
          <div className="banner outage-note">
            <p>
              {t.rich("note", {
                strong: (chunks) => <strong>{chunks}</strong>,
                code: (chunks) => <code>{chunks}</code>,
              })}
            </p>
          </div>
          <OutagePolicyManager
            initialRows={rows}
            initialSummary={summary}
            summaryNote={summaryNote}
          />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/admin/outage-policy", OutagePolicyPage);
