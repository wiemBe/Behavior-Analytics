// One WAF source's endpoints: which are tracked, and how adequate is each
// baseline?

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { WafEndpointsTable } from "@/components/waf/WafEndpointsTable";
import { validationView } from "@/components/waf/wafSourceHealth";
import {
  ApiError,
  api,
  type PagedWafEndpoints,
  type WafSourceDetail,
} from "@/lib/api";
import { formatCount } from "@/lib/behavior";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { withRouteMessages } from "@/i18n/RouteMessages";
import Link from "next/link";

import "../../waf.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("waf.source");
  return { title: t("metaTitle") };
}

const PAGE_SIZE = 50;

function Problem({ title, text, pageTitle }: { title: string; text: React.ReactNode; pageTitle: string }) {
  return (
    <>
      <PageHeader guide={guideFor("/behavior/waf/sources/[id]")} title={pageTitle} />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

async function WafSourceEndpointsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const t = await getTranslations("waf.source");
  const i18n = await getBehaviorI18n();
  const { locale } = i18n;
  const { id } = await params;
  const query = await searchParams;
  const activeOnly = query.active === "1";
  const parsedOffset = Number(query.offset ?? 0);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;

  let source: WafSourceDetail;
  let endpoints: PagedWafEndpoints;
  try {
    [source, endpoints] = await Promise.all([
      api.wafBehaviorSource(id),
      api.wafBehaviorEndpoints(id, {
        active_only: activeOnly || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return (
        <Problem
          pageTitle={t("title")}
          title={t("notFoundTitle")}
          text={
            <>
              {t("notFoundText")} <Link href="/behavior/waf">{t("openBehavior")}</Link>
            </>
          }
        />
      );
    }
    if (err instanceof ApiError && err.status === 403) {
      return (
        <Problem
          pageTitle={t("title")}
          title={t("forbiddenTitle")}
          text={t("forbiddenText")}
        />
      );
    }
    if (err instanceof ApiError && err.status === 401) {
      return (
        <Problem
          pageTitle={t("title")}
          title={t("expiredTitle")}
          text={t("expiredText")}
        />
      );
    }
    return (
      <Problem
        pageTitle={t("title")}
        title={t("failedTitle")}
        text={t("failedText")}
      />
    );
  }

  const v = validationView(source, i18n);
  const filterParams = { active: activeOnly ? "1" : undefined };

  return (
    <>
      <PageHeader
        guide={guideFor("/behavior/waf/sources/[id]")}
        title={source.log_source_name}
        description={t("description")}
        meta={
          <>
            <span className={`pill pill-strong ${v.tone}`}>{v.label}</span>
            <span>{v.detail}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span>{source.enabled ? t("profileEnabled") : t("profileDisabled")}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span className="num">
              {t("endpointsActive", {
                active: formatCount(source.active_endpoints, locale),
                ceiling: formatCount(source.active_endpoint_ceiling, locale),
              })}
            </span>
          </>
        }
        actions={
          <Link href="/behavior/waf" className="action secondary button-link">
            {t("allSources")}
          </Link>
        }
      />

      {!source.virtual_host_available && (
        <p className="banner">
          {t("noVirtualHost")}
        </p>
      )}

      <nav className="range-selector waf-filter" aria-label={t("filterLabel")}>
        <span className="muted">{t("show")}</span>
        <Link
          href={`/behavior/waf/sources/${id}`}
          className={`range-option${activeOnly ? "" : " range-option-active"}`}
          aria-current={activeOnly ? undefined : "page"}
        >
          {t("all")}
        </Link>
        <Link
          href={`/behavior/waf/sources/${id}?active=1`}
          className={`range-option${activeOnly ? " range-option-active" : ""}`}
          aria-current={activeOnly ? "page" : undefined}
        >
          {t("activeOnly")}
        </Link>
      </nav>

      {endpoints.items.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>
              {activeOnly ? t("emptyActiveTitle") : t("emptyTitle")}
            </strong>
          </p>
          <p>
            {source.validation_current && source.enabled
              ? t("emptyCollected")
              : t("emptyNotCollected")}
          </p>
        </div>
      ) : (
        <div className="stack-6">
          <div className="waf-endpoints">
            <WafEndpointsTable endpoints={endpoints.items} />
          </div>
          <Pagination
            total={endpoints.total}
            limit={PAGE_SIZE}
            offset={offset}
            basePath={`/behavior/waf/sources/${id}`}
            params={filterParams}
          />
        </div>
      )}
    </>
  );
}

export default withRouteMessages("/behavior/waf/sources/[id]", WafSourceEndpointsPage);
