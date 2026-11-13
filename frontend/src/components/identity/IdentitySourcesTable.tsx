// The identity overview: one table of sources (profile, newest bucket, collection state, open
// incidents) and one of the catalogue event counts over the chosen range.
//
// Sources that need attention are listed first. A figure that was not measured is the dash with a
// word for the analyst, never 0; a count over buckets some of which were truncated, uncollected
// or missing is "at least". An incident count that could not be read is not a zero either.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { sevTone, type IdentitySource } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { FormatLocale } from "@/lib/format";

import { IdentityStateBadge } from "./IdentityStateBadge";
import {
  IDENTITY_EVENTS,
  MISSING,
  bucketState,
  identityHref,
  isFailing,
  notCollectedWhy,
  sortByAttention,
  type EventTotal,
  type IdentityRange,
  type IncidentCounts,
} from "./identityFormat";

import "@/components/ui/controls.css";
import "./identity.css";

/** What the overview could read for each source beyond the list itself. */
export interface SourceReads {
  /** Active incidents by source; null when the incident list could not be read. */
  incidents: Map<string, IncidentCounts> | null;
  /** Event totals by source id; a source missing here was not read (failed or over the cap). */
  totals: Map<string, EventTotal[]>;
  /** Source ids whose counts read failed, as opposed to not attempted. */
  failed: ReadonlySet<string>;
}

function Dash({ meaning }: { meaning: string }) {
  return (
    <span className="muted">
      {MISSING}
      <span className="sr-only"> {meaning}</span>
    </span>
  );
}

function ProfileCell({ s }: { s: IdentitySource }) {
  const t = useTranslations("identity.table");
  const why = notCollectedWhy(s.profile);
  return (
    <>
      <span className={`pill pill-quiet ${why === null ? "ok" : "warn"}`}>
        {why === null ? t("profile.collected") : t(`profile.${why}`)}
      </span>
      <span className="cell-sub">{t("profileVersion", { version: s.profile.profile_version })}</span>
    </>
  );
}

function LatestCell({ s }: { s: IdentitySource }) {
  const t = useTranslations("identity.table");
  const locale = useLocale() as FormatLocale;
  const b = s.last_bucket;
  const state = bucketState(b);
  return (
    <>
      {b ? <UtcTime value={b.bucket_start} variant="compact" className="nowrap" /> : <Dash meaning={t("noBucketMeaning")} />}
      <span className="cell-sub">
        <IdentityStateBadge state={state} />
      </span>
      {b === null && <span className="cell-sub">{t("noBucketYet")}</span>}
      {b && b.population !== null && (
        <span className="cell-sub">{t("population", { count: formatCount(b.population, locale) })}</span>
      )}
      {isFailing(s.collection) && (
        <span className="cell-sub">
          <span className="pill pill-quiet crit">{t("failing")}</span>{" "}
          {t("failures", { count: s.collection.consecutive_failures ?? 0 })}
        </span>
      )}
    </>
  );
}

function IncidentsCell({ s, reads }: { s: IdentitySource; reads: SourceReads }) {
  const t = useTranslations("identity.table");
  const { severityPill } = useEnumLabels();
  const locale = useLocale() as FormatLocale;
  if (reads.incidents === null) return <Dash meaning={t("incidentsNotRead")} />;
  const counts = reads.incidents.get(s.profile.log_source_id);
  if (!counts) return <span>{t("incidentsNone")}</span>;
  return (
    <>
      <Link
        href={`/anomalies?subject_kind=IDENTITY_SOURCE&active_only=true&log_source_id=${s.profile.log_source_id}`}
      >
        {t("incidentsOpen", { count: formatCount(counts.total, locale) })}
      </Link>
      {counts.bySeverity.map((x) => (
        <span key={x.severity} className="cell-sub">
          <span className={`pill pill-quiet ${sevTone(x.severity)}`}>
            {severityPill(x.severity)}
          </span>{" "}
          <span className="num">{formatCount(x.count, locale)}</span>
        </span>
      ))}
    </>
  );
}

export function IdentitySourcesTable({
  sources,
  reads,
}: {
  sources: IdentitySource[];
  reads: SourceReads;
}) {
  const t = useTranslations("identity.table");
  const rows = sortByAttention(sources);
  return (
    <TableScroll label={t("label")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("source")}</th>
            <th scope="col">{t("profileHead")}</th>
            <th scope="col">
              {t("latest")}
              <span className="cell-sub">{t("latestSub")}</span>
            </th>
            <th scope="col">{t("incidents")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.profile.log_source_id}>
              <td className="cell-name">
                <Link href={`/behavior/identity/sources/${s.profile.log_source_id}`}>
                  {s.profile.log_source_name}
                </Link>
                {s.profile.product_label && <span className="cell-sub">{s.profile.product_label}</span>}
              </td>
              <td>
                <ProfileCell s={s} />
              </td>
              <td>
                <LatestCell s={s} />
              </td>
              <td>
                <IncidentsCell s={s} reads={reads} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function CountCell({ total, enabled, locale }: { total: EventTotal | undefined; enabled: boolean; locale: FormatLocale }) {
  const t = useTranslations("identity.counts");
  if (!enabled) return <span className="muted">{t("off")}</span>;
  if (!total || total.total === null) return <Dash meaning={t("notMeasured")} />;
  return (
    <span className="num">
      {total.atLeast ? `≥ ${formatCount(total.total, locale)}` : formatCount(total.total, locale)}
      {total.atLeast && <span className="sr-only"> {t("atLeast")}</span>}
    </span>
  );
}

export function IdentityCountsTable({
  sources,
  reads,
  range,
}: {
  sources: IdentitySource[];
  reads: SourceReads;
  range: IdentityRange;
}) {
  const t = useTranslations("identity.counts");
  const locale = useLocale() as FormatLocale;
  const rows = sortByAttention(sources);
  return (
    <TableScroll label={t("label")}>
      <table className="table-dense identity-counts">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("source")}</th>
            {IDENTITY_EVENTS.map((id) => (
              <th key={id} scope="col" className="num">
                {id}
                <span className="cell-sub">{t(`short.${id}`)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const id = s.profile.log_source_id;
            const totals = reads.totals.get(id);
            return (
              <tr key={id}>
                <td className="cell-name">
                  <Link href={identityHref(`/behavior/identity/sources/${id}`, { range })}>
                    {s.profile.log_source_name}
                  </Link>
                  {totals && totals[0] && totals[0].unmeasured > 0 && (
                    <span className="cell-sub">
                      {t("unmeasured", {
                        missing: formatCount(totals[0].unmeasured, locale),
                        slots: formatCount(totals[0].slots, locale),
                      })}
                    </span>
                  )}
                </td>
                {totals ? (
                  IDENTITY_EVENTS.map((event) => (
                    <td key={event} className="num">
                      <CountCell
                        total={totals.find((x) => x.eventId === event)}
                        enabled={s.profile.enabled_event_ids.includes(event)}
                        locale={locale}
                      />
                    </td>
                  ))
                ) : (
                  <td colSpan={IDENTITY_EVENTS.length} className="muted">
                    {reads.failed.has(id) ? t("readFailed") : t("notRead")}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
