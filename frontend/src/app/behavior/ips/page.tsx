// IPS behavior: which IPS sources are healthy enough to judge, and where is admission overflowing?
//
// One table, sources that need attention first. A source whose profile is not VALID for its
// current version is not collected, so it is not being judged -- that is said in the first line,
// not left for the analyst to infer from rows. The list takes no time range: the latest bucket is
// the newest collected, so a stale source is reported as stale instead of vanishing.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Pagination } from "@/components/Pagination";
import { IpsSourcesTable } from "@/components/ips/IpsSourcesTable";
import { isJudged } from "@/components/ips/ipsSourceHealth";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, rateLimitedText, type PagedIpsSources } from "@/lib/api";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { plainCount } from "@/lib/figureText";
import { guideFor } from "@/lib/guideRoutes";

import "@/components/ips/ips.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("ips.behavior");
  return { title: t("metaTitle") };
}

const PAGE_SIZE = 50;

async function IpsBehaviorPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const t = await getTranslations("ips.behavior");
  const say = asTextTranslator(await getTranslations());
  const i18n = await getBehaviorI18n();
  const count = (value: number) => plainCount(value, i18n.locale);
  const params = await searchParams;
  const parsedOffset = Number(params.offset ?? 0);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;

  let page: PagedIpsSources | null = null;
  let error: string | null = null;
  try {
    page = await api.ipsBehaviorSources({ limit: PAGE_SIZE, offset });
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("expired")
            : t("failed");
  }

  const items = page?.items ?? [];
  const judged = items.filter(isJudged).length;
  // The judged count is taken over the rows on this page, so it is only a share of the whole
  // list when the list fits on one page.
  const total = page?.total ?? items.length;
  const listed = items.length;
  const paginated = total > listed;

  return (
    <>
      <PageHeader
        guide={guideFor("/behavior/ips")}
        title={t("title")}
        description={t("description")}
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errorTitle")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>{t("emptyText")}</p>
        </div>
      ) : (
        <div className="stack-6">
          <p className="ips-lead">
            {t.rich("lead", {
              judged: count(judged),
              listed: count(listed),
              total: count(total),
              pages: paginated ? "page" : "one",
              num: (chunks) => <strong className="num">{chunks}</strong>,
            })}
          </p>

          <div className="ips-sources">
            <IpsSourcesTable sources={items} />
          </div>

          <Pagination total={total} limit={PAGE_SIZE} offset={offset} basePath="/behavior/ips" />
        </div>
      )}
    </>
  );
}

export default withRouteMessages("/behavior/ips", IpsBehaviorPage);
