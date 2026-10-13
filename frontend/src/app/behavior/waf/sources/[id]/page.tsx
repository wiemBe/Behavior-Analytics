// One WAF source's endpoints: which are tracked, and how adequate is each
// baseline?

import type { Metadata } from "next";

import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { WafEndpointsTable } from "@/components/waf/WafEndpointsTable";
import { validationView } from "@/components/waf/wafSourceHealth";
import {
  ApiError,
  api,
  type PagedWafEndpoints,
  type WafSourceDetail,
} from "@/lib/api";
import { formatCount } from "@/lib/behavior";
import Link from "next/link";

export const metadata: Metadata = {
  title: "WAF source endpoints",
};

const PAGE_SIZE = 50;

function Problem({ title, text }: { title: string; text: string }) {
  return (
    <>
      <PageHeader title="WAF source endpoints" />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

export default async function WafSourceEndpointsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
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
          title="This WAF source does not exist."
          text="No WAF behavior profile matches this address. It may have been removed, or the link may be wrong. Open WAF behavior to see the sources that exist."
        />
      );
    }
    if (err instanceof ApiError && err.status === 403) {
      return (
        <Problem
          title="You do not have permission to view WAF behavior."
          text="Ask an administrator for the read:anomalies permission. Nothing was changed."
        />
      );
    }
    if (err instanceof ApiError && err.status === 401) {
      return (
        <Problem
          title="Your session has expired."
          text="Sign in again to continue. Nothing was changed."
        />
      );
    }
    return (
      <Problem
        title="This source's endpoints could not be loaded."
        text="The backend may be unreachable. Check that it is running, then reload the page. Nothing was changed."
      />
    );
  }

  const v = validationView(source);
  const filterParams = { active: activeOnly ? "1" : undefined };

  return (
    <>
      <PageHeader
        title={source.log_source_name}
        description="Tracked endpoints and the adequacy of each baseline. Times are UTC."
        meta={
          <>
            <span className={`pill pill-strong ${v.tone}`}>{v.label}</span>
            <span>{v.detail}</span>
            <span>{source.enabled ? "Profile enabled" : "Profile disabled"}</span>
            <span className="num">
              {formatCount(source.active_endpoints)} of {formatCount(source.active_endpoint_ceiling)}{" "}
              endpoints active
            </span>
          </>
        }
        actions={
          <Link href="/behavior/waf" className="action secondary button-link">
            All WAF sources
          </Link>
        }
      />

      {!source.virtual_host_available && (
        <p className="banner">
          This source does not expose a virtual host, so an endpoint may combine traffic for
          different applications that share the path.
        </p>
      )}

      <nav className="range-selector" aria-label="Endpoint filter">
        <span className="muted">Show</span>
        <Link
          href={`/behavior/waf/sources/${id}`}
          className={`range-option${activeOnly ? "" : " range-option-active"}`}
          aria-current={activeOnly ? undefined : "page"}
        >
          All endpoints
        </Link>
        <Link
          href={`/behavior/waf/sources/${id}?active=1`}
          className={`range-option${activeOnly ? " range-option-active" : ""}`}
          aria-current={activeOnly ? "page" : undefined}
        >
          Active only
        </Link>
      </nav>

      {endpoints.items.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>
              {activeOnly ? "No active endpoint is tracked for this source." : "No endpoint is tracked for this source."}
            </strong>
          </p>
          <p>
            {source.validation_current && source.enabled
              ? "Endpoints appear once a collected bucket admits them. None has been admitted yet, so nothing is being judged."
              : "This source is not collected (its profile is disabled or not VALID for the current version), so no endpoint can be tracked."}
          </p>
        </div>
      ) : (
        <div className="stack-6">
          <WafEndpointsTable endpoints={endpoints.items} />
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
