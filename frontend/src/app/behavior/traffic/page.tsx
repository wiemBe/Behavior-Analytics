// Traffic by destination: which WAF endpoints and which IPS destination IPs receive the most
// events per second?
//
// One ranked table per view, the average rate drawn as a bar. The first screen answers the
// question; a window that cannot answer it (no complete bucket, no collection, no permission)
// says so where the table would be, and never draws a zero.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { asTextTranslator } from "@/i18n/textTranslator";

import { PageHeader } from "@/components/ui/PageHeader";
import { TrafficBody } from "@/components/traffic/TrafficBody";
import { TrafficControls, type TrafficSourceChoice } from "@/components/traffic/TrafficControls";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  api,
  ipsApi,
  rateLimitedText,
  type IpsTrafficItem,
  type TrafficPage,
  type WafTrafficItem,
} from "@/lib/api";
import { parseTrafficQuery } from "@/lib/traffic";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("traffic");
  return { title: t("meta") };
}

/** The sources a view can show: those with a profile, the collected ones first. */
async function listSources(view: "waf" | "ips"): Promise<TrafficSourceChoice[]> {
  if (view === "waf") {
    const page = await api.wafBehaviorSources({ limit: 100 });
    const rank = (s: { enabled: boolean; validation_current: boolean }) =>
      s.enabled && s.validation_current ? 0 : 1;
    return [...page.items]
      .sort((a, b) => rank(a) - rank(b))
      .map((s) => ({ id: s.log_source_id, name: s.log_source_name }));
  }
  const sources = await ipsApi.sources();
  const rank = (s: { enabled: boolean; collection_eligible: boolean }) =>
    s.enabled && s.collection_eligible ? 0 : 1;
  return [...sources]
    .sort((a, b) => rank(a) - rank(b))
    .map((s) => ({ id: s.log_source_id, name: s.log_source_name }));
}

async function TrafficPageView({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("traffic");
  const say = asTextTranslator(await getTranslations());
  const query = parseTrafficQuery(await searchParams);
  const { view } = query;

  const failure = (err: unknown, missing?: string): string =>
    err instanceof ApiError && err.status === 429
      ? rateLimitedText(err, say)
      : err instanceof ApiError && err.status === 403
        ? t("errors.forbidden")
        : err instanceof ApiError && err.status === 401
          ? t("errors.expired")
          : err instanceof ApiError && err.status === 404 && missing
            ? missing
            : t("errors.failed");

  let sources: TrafficSourceChoice[] = [];
  let sourcesError: string | null = null;
  try {
    sources = await listSources(view);
  } catch (err) {
    // A source named in the link can still be read; only the choice between sources is lost.
    if (!query.source) {
      sourcesError =
        err instanceof ApiError && [401, 403, 429].includes(err.status)
          ? failure(err)
          : t("errors.sourcesFailed");
    }
  }
  const sourceId = query.source || sources[0]?.id || "";

  let data: TrafficPage<WafTrafficItem> | TrafficPage<IpsTrafficItem> | null = null;
  let error: string | null = sourcesError;
  if (!error && sourceId) {
    const params = {
      window: query.window,
      sort: query.sort,
      order: query.order,
      limit: query.limit,
      offset: query.offset,
    };
    try {
      data =
        view === "waf"
          ? await api.wafTraffic(sourceId, params)
          : await api.ipsTraffic(sourceId, params);
    } catch (err) {
      error = failure(err, t("errors.notFound"));
    }
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/behavior/traffic")}
        title={t("title")}
        description={t("description")}
      />
      <TrafficControls
        query={query}
        sourceId={sourceId}
        sources={sources}
        basis={data ? { kind: "data", page: data } : { kind: "error" }}
      />
      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errors.title")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : data ? (
        <TrafficBody view={view} query={query} data={data} sourceId={sourceId} />
      ) : (
        <div className="notice" role="status">
          <p>
            <strong>{view === "waf" ? t("noSource.titleWaf") : t("noSource.titleIps")}</strong>
          </p>
          <p>{view === "waf" ? t("noSource.textWaf") : t("noSource.textIps")}</p>
          <p>
            <Link href={view === "waf" ? "/admin/waf-sources" : "/admin/ips-sources"}>
              {view === "waf" ? t("notCollected.linkWaf") : t("notCollected.linkIps")}
            </Link>
          </p>
        </div>
      )}
    </>
  );
}

export default withRouteMessages("/behavior/traffic", TrafficPageView);
