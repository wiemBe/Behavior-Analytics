// Which sources and classes page a human, which are only recorded, and which go into the digest.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  alertPolicyApi,
  rateLimitedText,
  type AlertPolicyRow,
  type DigestStatus,
} from "@/lib/api";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";

import { AlertPolicyManager } from "./AlertPolicyManager";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("alertPolicy");
  return { title: t("meta") };
}

async function AlertPolicyPage() {
  const t = await getTranslations("alertPolicy");
  const say = asTextTranslator(await getTranslations());
  let rows: AlertPolicyRow[] = [];
  let digest: DigestStatus | null = null;
  let digestNote: string | null = null;
  let failure: string | null = null;

  const [policy, status] = await Promise.allSettled([alertPolicyApi.list(), alertPolicyApi.digest()]);
  if (policy.status === "fulfilled") {
    rows = policy.value;
  } else {
    const err = policy.reason;
    failure =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("errors.forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("errors.expired")
            : t("errors.failed");
  }
  if (status.status === "fulfilled") {
    digest = status.value;
  } else {
    const err = status.reason;
    digestNote =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("digest.forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("digest.expired")
            : t("digest.failed");
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/admin/alert-policy")}
        title={t("title")}
        description={t("description")}
      />
      {failure ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errors.title")}</strong>
          </p>
          <p>{failure}</p>
        </div>
      ) : (
        <AlertPolicyManager initialRows={rows} initialDigest={digest} digestNote={digestNote} />
      )}
    </>
  );
}

export default withRouteMessages("/admin/alert-policy", AlertPolicyPage);
