// Identity: which identity sources need attention now?
//
// One table of sources, those that need attention first: a failing collector, a source that is not
// collected, a truncated or partial newest bucket. A second table gives the catalogue event counts
// over the chosen range. A source whose profile is not enabled and validated for its current
// version is not collected, and the first line says so rather than leaving rows to imply it. A
// figure that was not read or not measured is the dash, never 0 (the honesty states in the Guide).

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import {
  IdentityCountsTable,
  IdentitySourcesTable,
  type SourceReads,
} from "@/components/identity/IdentitySourcesTable";
import { IdentityRangeSelector } from "@/components/identity/IdentityRangeSelector";
import {
  IDENTITY_EVENTS,
  bucketState,
  eventTotals,
  incidentsBySource,
  isFailing,
  notCollectedWhy,
  parseRange,
  slotsIn,
  sortByAttention,
  windowOf,
  type EventTotal,
} from "@/components/identity/identityFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  ApiError,
  api,
  identityApi,
  rateLimitedText,
  type AnomalySummary,
  type IdentitySource,
} from "@/lib/api";
import { guideFor } from "@/lib/guideRoutes";
import { permissionOf } from "@/lib/suppression";

import "@/components/identity/identity.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("identity.overview");
  return { title: t("metaTitle") };
}

/** Sources whose counts are read in one request each; the rest say they were not read. */
const COUNTS_READ_CAP = 25;
const INCIDENT_LIMIT = 200;

function Problem({ title, text, role = "alert" }: { title: string; text: string; role?: "alert" | "status" }) {
  return (
    <div className="notice" role={role}>
      <p>
        <strong>{title}</strong>
      </p>
      <p>{text}</p>
    </div>
  );
}

async function IdentityOverviewPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("identity.overview");
  const say = asTextTranslator(await getTranslations());
  const params = await searchParams;
  const range = parseRange(params.range);
  const nowMs = Date.now();

  let sources: IdentitySource[] = [];
  try {
    sources = await identityApi.sources();
  } catch (err) {
    let text = t("failedText");
    if (err instanceof ApiError && err.status === 429) text = rateLimitedText(err, say);
    else if (err instanceof ApiError && err.status === 403) {
      text = t("forbiddenText", { permission: permissionOf(err) ?? "read:identity" });
    } else if (err instanceof ApiError && err.status === 401) text = t("expiredText");
    const forbidden = err instanceof ApiError && err.status === 403;
    return (
      <>
        <PageHeader guide={guideFor("/behavior/identity")} title={t("title")} description={t("description")} />
        <Problem title={forbidden ? t("forbiddenTitle") : t("failedTitle")} text={text} />
      </>
    );
  }

  if (sources.length === 0) {
    return (
      <>
        <PageHeader guide={guideFor("/behavior/identity")} title={t("title")} description={t("description")} />
        <div className="notice" role="status">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>
            {t("emptyText")} <Link href="/log-sources">{t("emptyLink")}</Link>
          </p>
        </div>
      </>
    );
  }

  const ordered = sortByAttention(sources);
  const window = windowOf(range, nowMs);
  const reading = ordered.slice(0, COUNTS_READ_CAP);

  const [incidentRead, detailReads] = await Promise.all([
    api
      .anomalies({ subject_kind: "IDENTITY_SOURCE", active_only: true, limit: INCIDENT_LIMIT })
      .then((page): { items: AnomalySummary[]; total: number } => page)
      .catch(() => null),
    Promise.allSettled(
      reading.map((s) =>
        identityApi.source(s.profile.log_source_id, { start: window.start, end: window.end }),
      ),
    ),
  ]);

  const totals = new Map<string, EventTotal[]>();
  const failed = new Set<string>();
  detailReads.forEach((result, index) => {
    const id = reading[index].profile.log_source_id;
    if (result.status === "rejected") {
      failed.add(id);
      return;
    }
    const seconds = reading[index].last_bucket?.bucket_seconds ?? 300;
    totals.set(
      id,
      eventTotals(
        result.value.series,
        IDENTITY_EVENTS,
        slotsIn(window.startMs, window.endMs, seconds),
      ),
    );
  });
  const reads: SourceReads = {
    incidents: incidentRead ? incidentsBySource(incidentRead.items) : null,
    totals,
    failed,
  };

  const collected = sources.filter((s) => notCollectedWhy(s.profile) === null).length;
  const attention = sources.filter(
    (s) =>
      isFailing(s.collection) ||
      notCollectedWhy(s.profile) !== null ||
      bucketState(s.last_bucket) !== "COMPLETE",
  ).length;
  const incidentsCapped = incidentRead !== null && incidentRead.total > incidentRead.items.length;

  return (
    <>
      <PageHeader guide={guideFor("/behavior/identity")} title={t("title")} description={t("description")} />
      <div className="stack-6">
        <p className="identity-lead">
          {t.rich("lead", {
            collected,
            total: sources.length,
            attention,
            num: (chunks) => <strong className="num">{chunks}</strong>,
          })}
        </p>
        {incidentRead === null && (
          <p className="banner warn" role="status">
            {t("incidentsFailed")}
          </p>
        )}
        {incidentsCapped && (
          <p className="banner warn" role="status">
            {t("incidentsCapped", { shown: incidentRead.items.length, total: incidentRead.total })}
          </p>
        )}

        <section className="stack-6">
          <h2 className="section-title">{t("sourcesHeading")}</h2>
          <IdentitySourcesTable sources={sources} reads={reads} />
        </section>

        <section className="stack-6">
          <h2 className="section-title">{t("countsHeading")}</h2>
          <div className="identity-controls">
            <IdentityRangeSelector path="/behavior/identity" active={range} />
          </div>
          <p className="subtitle">{t("countsNote")}</p>
          {sources.length > COUNTS_READ_CAP && (
            <p className="banner warn" role="status">
              {t("countsCapped", { cap: COUNTS_READ_CAP, total: sources.length })}
            </p>
          )}
          <IdentityCountsTable sources={sources} reads={reads} range={range} />
        </section>
      </div>
    </>
  );
}

export default withRouteMessages("/behavior/identity", IdentityOverviewPage);
