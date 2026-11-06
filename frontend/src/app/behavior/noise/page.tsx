// Noisy sources: which log sources keep raising anomalies, and how much of that is noise?
//
// One ranked table with the window and the order in the URL. A window that cannot answer (empty,
// partial, failed, forbidden) says so where the table would be, and never draws a zero.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { NoiseBody } from "@/components/noise/NoiseBody";
import { NoiseControls } from "@/components/noise/NoiseControls";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, rateLimitedText, type NoiseSourcesPage } from "@/lib/api";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import { NOISE_PAGE_SIZE, parseNoiseQuery } from "@/lib/noise";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("noise");
  return { title: t("meta") };
}

async function NoisePageView({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("noise");
  const say = asTextTranslator(await getTranslations());
  const query = parseNoiseQuery(await searchParams);

  let data: NoiseSourcesPage | null = null;
  let error: string | null = null;
  try {
    data = await api.noiseSources({
      window: query.window,
      order: query.order,
      limit: NOISE_PAGE_SIZE,
      offset: query.offset,
    });
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("errors.forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("errors.expired")
            : t("errors.failed");
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/behavior/noise")}
        title={t("title")}
        description={t("description")}
      />
      <NoiseControls query={query} />
      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errors.title")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : data ? (
        <NoiseBody query={query} data={data} />
      ) : null}
    </>
  );
}

export default withRouteMessages("/behavior/noise", NoisePageView);
