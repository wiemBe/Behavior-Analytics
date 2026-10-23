// WAF behavior: which WAF sources are healthy enough to judge, and where is
// admission overflowing?
//
// One table, sources that need attention first. A source whose profile is not
// VALID for its current version is not collected, so it is not being judged --
// that is said in the first line, not left for the analyst to infer from rows.

import type { Metadata } from "next";

import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { WafRangeSelector } from "@/components/waf/WafRangeSelector";
import { WafSourcesTable } from "@/components/waf/WafSourcesTable";
import { isJudged } from "@/components/waf/wafSourceHealth";
import { parseWafRange } from "@/components/waf/wafRange";
import { ApiError, api, type PagedWafSources } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import "./waf.css";

export const metadata: Metadata = {
  title: "WAF behavior",
};

const PAGE_SIZE = 50;

export default async function WafBehaviorPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const range = parseWafRange(params.range);
  const parsedOffset = Number(params.offset ?? 0);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;

  const until = new Date();
  const since = new Date(until.getTime() - range.hours * 3600_000);

  let page: PagedWafSources | null = null;
  let error: string | null = null;
  try {
    page = await api.wafBehaviorSources({
      since: since.toISOString(),
      until: until.toISOString(),
      limit: PAGE_SIZE,
      offset,
    });
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "You do not have permission to view WAF behavior."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "WAF sources could not be loaded, so nothing is shown. The backend may be unreachable; check that it is running, then reload the page. Nothing was changed.";
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
        title="WAF behavior"
        description="Whether each WAF source is collected and judged, and where endpoint admission is overflowing. Times are UTC."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>WAF behavior could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>No WAF source has a behavior profile.</strong>
          </p>
          <p>
            Nothing is collected or judged until a log source is mapped and validated in WAF
            sources administration. An empty list here is not a statement that WAF traffic is
            normal.
          </p>
        </div>
      ) : (
        <div className="stack-6">
          <p className="waf-lead">
            <strong className="num">
              {judged} of {listed}
            </strong>{" "}
            {listed === 1 ? "source is" : "sources are"}
            {paginated ? " listed on this page and" : ""} collected and judged: profile enabled and
            valid for its current version. The others produce no verdicts.
            {paginated && ` The list has ${total} sources in all; the other pages are not counted here.`}
          </p>

          <div className="waf-range">
            <WafRangeSelector active={range.key} />
            {page && (
              <p className="subtitle">
                Admission overflow covers{" "}
                <UtcTime value={page.range_start} /> to{" "}
                <UtcTime value={page.range_end} />.
              </p>
            )}
          </div>

          <div className="waf-sources">
            <WafSourcesTable sources={items} />
          </div>

          <Pagination
            total={total}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/behavior/waf"
            params={{ range: range.key === "24h" ? undefined : range.key }}
          />
        </div>
      )}
    </>
  );
}
