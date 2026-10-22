// The log-source fleet by class.
//
// The question: which classes of log source are off their normal volume right
// now, and what needs review? The first screen is the per-class table with the
// review queue named above it. Internal (QRadar) sources are counted apart, as
// the API returns them.

import type { Metadata } from "next";
import Link from "next/link";

import { FleetClassTable } from "@/components/fleet/FleetClassTable";
import { WindowSwitch } from "@/components/fleet/WindowSwitch";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, fleetApi, type FleetSummary } from "@/lib/api";
import { formatDateTime } from "@/lib/health";
import { parseWindow, windowName } from "@/lib/fleet";

export const metadata: Metadata = {
  title: "Fleet",
};

function queueItem(count: number, singular: string, plural: string, href: string) {
  const text = `${count.toLocaleString("en-US")} ${count === 1 ? singular : plural}`;
  return count > 0 ? <Link href={href}>{text}</Link> : <span>{text}</span>;
}

export default async function FleetPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const window = parseWindow((await searchParams).window);

  let summary: FleetSummary | null = null;
  let error: string | null = null;
  try {
    summary = await fleetApi.summary(window);
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "Your role cannot read the fleet summary (permission read:fleet). Nothing was changed."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "The fleet summary could not be loaded. The backend may be unreachable; check that it is running, then reload the page.";
  }

  return (
    <>
      {/* The summary's `window_end` is the request's own "now": the page has no data time. */}
      <ReportDataAge iso={null} />
      <PageHeader
        title="Log-source fleet"
        description="Volume per class of log source against its baseline, and the sources waiting for review. Times are UTC."
        actions={<WindowSwitch active={window} />}
      />

      {error || !summary ? (
        <div className="notice" role="alert">
          <p>
            <strong>The fleet summary could not be shown.</strong>
          </p>
          <p>{error ?? "The fleet summary could not be loaded."}</p>
        </div>
      ) : (
        <>
          <p className="banner" aria-label="Review queue">
            <span>
              <strong>Review queue:</strong>{" "}
              {queueItem(
                summary.new_sources,
                "new source",
                "new sources",
                "/fleet/sources?review_status=NEW",
              )}
              ,{" "}
              {queueItem(
                summary.conflict_sources,
                "source with a conflicting class",
                "sources with a conflicting class",
                "/fleet/sources?has_conflict=true",
              )}
              ,{" "}
              {queueItem(
                summary.unclassified_sources,
                "unclassified source",
                "unclassified sources",
                "/fleet/sources?class=UNCLASSIFIED",
              )}
              .
            </span>
          </p>

          <p className="subtitle fleet-window">
            Last {windowName(window)}: {formatDateTime(summary.window_start)} to{" "}
            {formatDateTime(summary.window_end)}, in {summary.bucket_seconds / 60}-minute buckets.
          </p>

          {summary.classes.length === 0 ? (
            <div className="notice">
              <p>
                <strong>No classes were returned.</strong>
              </p>
              <p>
                The summary lists every class that has at least one source. Run an inventory sync
                from the Log Sources page, then reload.
              </p>
            </div>
          ) : (
            <FleetClassTable classes={summary.classes} windowName={windowName(window)} />
          )}

          <ul className="table-notes">
            <li>
              Events and mean EPS count complete buckets only. Where the window is incomplete, the
              row says how many buckets are partial or missing.
            </li>
            <li>
              Expected EPS is the sum, over the sources that have a baseline, of each source&apos;s mean
              baseline median across every UTC weekday and hour cell the window spans. A source
              counts only when all of those cells are reliable.
            </li>
          </ul>

          <section aria-labelledby="fleet-internal">
            <h2 className="section-title" id="fleet-internal">
              QRadar internal sources
            </h2>
            <p className="subtitle">
              {summary.internal.sources.enabled} enabled, {summary.internal.sources.disabled}{" "}
              disabled. They are counted apart from the classes above and carry no volume figure
              here.
            </p>
          </section>
        </>
      )}
    </>
  );
}
