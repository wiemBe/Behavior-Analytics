// Which sources and classes page a human, which are only recorded, and which go into the digest.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  alertPolicyApi,
  rateLimitedText,
  suppressionApi,
  type AlertPolicyRow,
  type DigestStatus,
  type Suppression,
} from "@/lib/api";
import { readFailure, type ReadFailure } from "@/lib/suppression";
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

  let quiet: Suppression[] | null = null;
  let quietFailure: ReadFailure | null = null;

  const [policy, status, quietRead] = await Promise.allSettled([
    alertPolicyApi.list(),
    alertPolicyApi.digest(),
    suppressionApi.list(true),
  ]);
  if (quietRead.status === "fulfilled") {
    quiet = quietRead.value;
  } else {
    quietFailure = readFailure(quietRead.reason, say, "read:alerts");
  }
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
        <AlertPolicyManager
          initialRows={rows}
          initialDigest={digest}
          digestNote={digestNote}
          initialQuiet={quiet}
          quietFailure={quietFailure}
        />
      )}
    </>
  );
}

export default withRouteMessages("/admin/alert-policy", AlertPolicyPage);
