// One identity source: what did its stored events say over the window, and how whole is that?
//
// The counts of the chosen event ID are drawn in events per 5 minutes, with a gap and a shaded
// span wherever a bucket was not collected. The stored usernames are listed beneath, exactly as
// the rows hold them. The profile and, for `admin:identity`, its controls close the page. No
// expected range is drawn because the API serves no baseline for identity counts.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { holds } from "@/components/auth/access";
import { IdentityEventChart } from "@/components/identity/IdentityEventChart";
import { IdentityEventsTable } from "@/components/identity/IdentityEventsTable";
import { IdentityNamesTable } from "@/components/identity/IdentityNamesTable";
import { IdentityProfilePanel } from "@/components/identity/IdentityProfilePanel";
import { IdentityRangeSelector } from "@/components/identity/IdentityRangeSelector";
import {
  ADMIN_PERMISSION,
  IDENTITY_EVENTS,
  eventKey,
  eventTotals,
  namesIncomplete,
  namesOf,
  notCollectedWhy,
  parseEvent,
  parseRange,
  slotsIn,
  windowOf,
} from "@/components/identity/identityFormat";
import { buildSeriesPlot } from "@/components/identity/identitySeries";
import { PivotBlock } from "@/components/pivot/PivotBlock";
import { PageHeader } from "@/components/ui/PageHeader";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  ApiError,
  api,
  identityApi,
  rateLimitedText,
  type IdentitySourceDetail,
} from "@/lib/api";
import { guideFor } from "@/lib/guideRoutes";
import { pivotReads } from "@/lib/pivot";
import { refusalSentences } from "@/lib/refusal";
import { permissionOf } from "@/lib/suppression";

import "@/components/identity/identity.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("identity.source");
  return { title: t("metaTitle") };
}

/** Every identity bucket is five minutes wide (backend `IDENTITY_BUCKET_SECONDS`). */
const BUCKET_SECONDS = 300;

async function canAdminIdentity(): Promise<boolean> {
  try {
    const me = await api.caller();
    return holds(me.effective_permissions, ADMIN_PERMISSION);
  } catch {
    // A failed read of the grants is "no": the controls are withheld, never guessed.
    return false;
  }
}

async function IdentitySourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const raw = await searchParams;
  const t = await getTranslations("identity.source");
  const say = asTextTranslator(await getTranslations());
  const range = parseRange(raw.range);
  const window = windowOf(range, Date.now(), BUCKET_SECONDS);
  const path = `/behavior/identity/sources/${id}`;
  const header = (title: string, description?: string) => (
    <PageHeader
      guide={guideFor("/behavior/identity/sources/[id]")}
      title={title}
      description={description ?? t("description")}
      actions={
        <Link href="/behavior/identity" className="action secondary button-link">
          {t("allSources")}
        </Link>
      }
    />
  );
  const problem = (title: string, text: string) => (
    <>
      {header(t("title"))}
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );

  let detail: IdentitySourceDetail;
  try {
    detail = await identityApi.source(id, { start: window.start, end: window.end });
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      return problem(t("failedTitle"), rateLimitedText(err, say));
    }
    if (err instanceof ApiError && err.status === 403) {
      return problem(
        t("forbiddenTitle"),
        t("forbiddenText", { permission: permissionOf(err) ?? "read:identity" }),
      );
    }
    if (err instanceof ApiError && err.status === 401) {
      return problem(t("expiredTitle"), t("expiredText"));
    }
    if (err instanceof ApiError && err.status === 404) {
      // `identity.profile.not_found`: the log source exists and has no identity profile yet. Any
      // other 404 is a source that does not exist.
      const code = refusalSentences(err)[0]?.code;
      if (code === "identity.profile.not_found") {
        const [source, canAdmin] = await Promise.all([
          api.logSource(id).catch(() => null),
          canAdminIdentity(),
        ]);
        return (
          <>
            {header(source?.name ?? t("title"), t("noProfileDescription"))}
            <IdentityProfilePanel logSourceId={id} profile={null} canAdmin={canAdmin} />
          </>
        );
      }
      return problem(t("notFoundTitle"), t("notFoundText"));
    }
    return problem(t("failedTitle"), t("failedText"));
  }

  const [canAdmin, pivot] = await Promise.all([canAdminIdentity(), pivotReads.logSource(id)]);
  const profile = detail.profile;
  const why = notCollectedWhy(profile);
  const slots = slotsIn(window.startMs, window.endMs, BUCKET_SECONDS);
  const enabled: number[] = IDENTITY_EVENTS.filter((e) => profile.enabled_event_ids.includes(e));
  const totals = eventTotals(detail.series, enabled, slots);
  const requested = parseEvent(raw.event);
  const selected = requested !== null && enabled.includes(requested) ? requested : (enabled[0] ?? null);
  const newest = detail.series.reduce<string | null>(
    (best, p) => (best === null || p.bucket_start > best ? p.bucket_start : best),
    null,
  );
  const events = await getTranslations("identity.events");
  const eventLabel = selected === null ? "" : `${selected} ${events(eventKey(selected))}`;

  return (
    <>
      {header(profile.log_source_name)}
      <div className="row page-meta">
        <span className={`pill pill-strong ${why === null ? "ok" : "warn"}`}>
          {why === null ? t("collected") : t(`why.${why}`)}
        </span>
        {newest ? (
          <span>
            {t("newestBucket")} <UtcTime value={newest} />
          </span>
        ) : (
          <span>{t("noBucketInRange")}</span>
        )}
        <span>
          <Link
            href={`/anomalies?subject_kind=IDENTITY_SOURCE&log_source_id=${id}`}
          >
            {t("incidentsLink")}
          </Link>
        </span>
      </div>
      {why !== null && <p className="banner warn">{t(`whyBanner.${why}`)}</p>}

      <div className="stack-6">
        <div className="identity-controls">
          <IdentityRangeSelector path={path} active={range} event={selected} />
        </div>
        <p className="subtitle">
          {t.rich("covers", {
            start: () => <UtcTime value={window.start} />,
            end: () => <UtcTime value={window.end} />,
          })}
        </p>

        <section className="stack-6">
          <h2 className="section-title">{t("eventsHeading")}</h2>
          {enabled.length === 0 ? (
            <div className="notice" role="status">
              <p>{t("noEvents")}</p>
            </div>
          ) : (
            <IdentityEventsTable path={path} range={range} totals={totals} selected={selected} />
          )}
        </section>

        {selected !== null && (
          <>
            <section className="stack-6">
              <h2 className="section-title">{t("chartHeading", { event: eventLabel })}</h2>
              <IdentityEventChart
                plot={buildSeriesPlot(
                  detail.series,
                  selected,
                  window.startMs,
                  window.endMs,
                  BUCKET_SECONDS,
                )}
                eventLabel={eventLabel}
              />
            </section>
            <section className="stack-6">
              <h2 className="section-title">{t("namesHeading", { event: eventLabel })}</h2>
              <IdentityNamesTable
                eventId={selected}
                names={namesOf(detail.series, selected)}
                incomplete={namesIncomplete(detail.series, selected)}
              />
            </section>
          </>
        )}

        <IdentityProfilePanel logSourceId={id} profile={profile} canAdmin={canAdmin} />
        {pivot && <PivotBlock pivot={pivot.pivot} failed={pivot.failed} />}
      </div>
    </>
  );
}

export default withRouteMessages("/behavior/identity/sources/[id]", IdentitySourcePage);
