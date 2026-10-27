// The log-source fleet by class.
//
// The question: which classes of log source are off their normal volume right
// now, and what needs review? The first screen is the per-class table with the
// review queue named above it. Internal (QRadar) sources are counted apart, as
// the API returns them.

import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";

import { FleetClassTable } from "@/components/fleet/FleetClassTable";
import { WindowSwitch } from "@/components/fleet/WindowSwitch";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, fleetApi, type FleetSummary } from "@/lib/api";
import { parseWindow, windowName } from "@/lib/fleet";
import { fleetTextOf, plainCount } from "@/lib/fleetText";
import { UtcTime } from "@/components/ui/UtcTime";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("fleet.meta");
  return { title: t("fleet") };
}

/** The full stop that ends the review-queue sentence (outside every item, so not translated). */
const STOP = ".";

function queueItem(count: number, text: string, href: string) {
  return count > 0 ? <Link href={href}>{text}</Link> : <span>{text}</span>;
}

export default async function FleetPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const window = parseWindow((await searchParams).window);
  const t = await getTranslations("fleet.page");
  const q = await getTranslations("fleet.queue");
  const locale = await getLocale();
  const text = fleetTextOf(await getTranslations(), locale);

  let summary: FleetSummary | null = null;
  let error: string | null = null;
  try {
    summary = await fleetApi.summary(window);
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? t("forbidden")
        : err instanceof ApiError && err.status === 401
          ? text.say("errors.sessionExpired")
          : t("unreachable");
  }

  return (
    <>
      {/* The summary's `window_end` is the request's own "now": the page has no data time. */}
      <ReportDataAge iso={null} />
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<WindowSwitch active={window} />}
      />

      {error || !summary ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("noticeHeading")}</strong>
          </p>
          <p>{error ?? t("loadFailed")}</p>
        </div>
      ) : (
        <>
          <p className="banner" aria-label={t("queueLabel")}>
            <span>
              <strong>{t("queueLead")}</strong>{" "}
              {queueItem(
                summary.new_sources,
                q("newSources", { count: summary.new_sources }),
                "/fleet/sources?review_status=NEW",
              )}
              ,{" "}
              {queueItem(
                summary.conflict_sources,
                q("conflictSources", { count: summary.conflict_sources }),
                "/fleet/sources?has_conflict=true",
              )}
              ,{" "}
              {queueItem(
                summary.unclassified_sources,
                q("unclassifiedSources", { count: summary.unclassified_sources }),
                "/fleet/sources?class=UNCLASSIFIED",
              )}
              {STOP}
            </span>
          </p>

          <p className="subtitle fleet-window">
            {t.rich("windowLine", {
              window: windowName(window, text),
              minutes: plainCount(summary.bucket_seconds / 60, text.locale),
              start: () => <UtcTime value={summary.window_start} />,
              end: () => <UtcTime value={summary.window_end} />,
            })}
          </p>

          {summary.classes.length === 0 ? (
            <div className="notice">
              <p>
                <strong>{t("noClassesHeading")}</strong>
              </p>
              <p>{t("noClassesBody")}</p>
            </div>
          ) : (
            <FleetClassTable classes={summary.classes} windowName={windowName(window, text)} />
          )}

          <ul className="table-notes">
            <li>{t("noteEvents")}</li>
            <li>{t("noteExpected")}</li>
          </ul>

          <section aria-labelledby="fleet-internal">
            <h2 className="section-title" id="fleet-internal">
              {t("internalHeading")}
            </h2>
            <p className="subtitle">
              {t("internalCounts", {
                enabled: plainCount(summary.internal.sources.enabled, text.locale),
                disabled: plainCount(summary.internal.sources.disabled, text.locale),
              })}
            </p>
          </section>
        </>
      )}
    </>
  );
}
