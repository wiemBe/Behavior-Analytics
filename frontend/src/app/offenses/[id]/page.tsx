import { api, type OffenseDetail, type OffenseHistoryPoint } from "@/lib/api";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatDuration } from "@/lib/behavior";
import { magnitudeTone, offenseStatusTone } from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";
import { formatCount } from "@/lib/format";
import type { Locale } from "@/i18n/locale";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";


import "../offenses.css";
import "./offense-detail.css";

/** A count that was not measured is a dash, never a zero. */
function count(n: number | null | undefined, locale: Locale): string {
  return n == null ? "—" : formatCount(n, locale);
}

/** A 0–10 score. Null is an em dash, with no unit attached to a missing measurement. */
function scoreOfTen(value: number | null | undefined, label: (value: number) => string): string {
  return value == null ? "—" : label(value);
}

function List({ label, values, empty }: { label: string; values: (string | number)[]; empty: string }) {
  return (
    <div className="detail-block">
      <h4>
        {label} <span className="muted">({values.length})</span>
      </h4>
      {values.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <ul className="chips">
          {values.map((v) => (
            <li key={String(v)} className="pill">
              {String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export async function generateMetadata() {
  const t = await getTranslations("offenses.meta");
  return { title: t("detail") };
}

export default async function OffenseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const offenseId = Number(id);
  const t = await getTranslations("offenses.detail");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;

  if (!Number.isFinite(offenseId)) {
    return (
      <>
        <PageHeader guide={guideFor("/offenses/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p>{t("invalid")}</p>
          <p>{t("invalidHelp")}</p>
        </div>
        <p>
          <Link href="/offenses">{t("back")}</Link>
        </p>
      </>
    );
  }

  let offense: OffenseDetail | null = null;
  let history: OffenseHistoryPoint[] | null = [];
  let missing = false;
  let error = false;

  try {
    offense = await api.offense(offenseId);
    // A missing history is not a missing offense — degrade rather than 404.
    history = await api.offenseHistory(offenseId).catch(() => null);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error) {
    return (
      <>
        <PageHeader guide={guideFor("/offenses/[id]")} title={t("detailTitle", { id: offenseId })} />
        <div className="notice" role={missing ? "status" : "alert"}>
          <p>
            {missing
              ? t("missing")
              : t("failed")}
          </p>
        </div>
        <p>
          <Link href="/offenses">{t("back")}</Link>
        </p>
      </>
    );
  }

  const o = offense!;

  return (
    <>
      <p className="muted">
        <Link href="/offenses">{t("crumb")}</Link>
      </p>
      {/* Text only. QRadar-sourced, sanitized server-side, escaped by React. */}
      <PageHeader
        guide={guideFor("/offenses/[id]")}
        title={t("detailTitle", { id: o.qradar_offense_id })}
        description={o.description?.trim() || t("noDescription")}
      />

      <h2>{t("scores")}</h2>
      <dl className="dimension-meta" aria-label={t("scoreLabel")}>
        <div>
          <dt>{t("magnitude")}</dt>
          <dd>
            <span className={`pill ${magnitudeTone(o.magnitude)}`}>
              {scoreOfTen(o.magnitude, (value) => t("ofTen", { value }))}
            </span>
            {o.magnitude != null && (
              <span
                className={`mag-bar ${magnitudeTone(o.magnitude)} mag-w-${Math.min(10, Math.max(0, Math.round(o.magnitude)))}`}
                aria-hidden="true"
              />
            )}
          </dd>
        </div>
        <div>
          <dt>{t("status")}</dt>
          <dd>
            <span className={`pill ${offenseStatusTone(o.status)}`}>
              {enumLabel(say, "offenseStatus", o.status)}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("severity")}</dt>
          <dd>{scoreOfTen(o.severity, (value) => t("ofTen", { value }))}</dd>
        </div>
        <div>
          <dt>{t("credibility")}</dt>
          <dd>{scoreOfTen(o.credibility, (value) => t("ofTen", { value }))}</dd>
        </div>
        <div>
          <dt>{t("relevance")}</dt>
          <dd>{scoreOfTen(o.relevance, (value) => t("ofTen", { value }))}</dd>
        </div>
        <div>
          <dt>{t("age")}</dt>
          <dd>{formatDuration(o.age_seconds, { t: say, locale })}</dd>
        </div>
      </dl>

      <h3>{t("attributes")}</h3>
      <TableScroll label={t("attributesLabel")}>
      <table className="kv-table" aria-label={t("attributesLabel")}>
        <tbody>
          <tr>
            <th>{t("assignedTo")}</th>
            <td>{o.assigned_to ?? <span className="muted">{t("unassigned")}</span>}</td>
          </tr>
          <tr>
            <th>{t("offenseType")}</th>
            <td>{o.offense_type_name ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("offenseSource")}</th>
            <td>{o.offense_source ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("sourceNetwork")}</th>
            <td>{o.source_network ?? "—"}</td>
          </tr>
          <tr>
            <th>{t("eventsFlows")}</th>
            <td>
              {count(o.event_count, locale)} / {count(o.flow_count, locale)}
            </td>
          </tr>
          <tr>
            <th>{t("sourcesDestinations")}</th>
            <td>
              {count(o.source_count, locale)} / {count(o.destination_count, locale)}
            </td>
          </tr>
          <tr>
            <th>{t("started")}</th>
            <td>{<UtcTime value={o.start_time} />}</td>
          </tr>
          <tr>
            <th>{t("lastUpdated")}</th>
            <td>{<UtcTime value={o.last_updated_time} />}</td>
          </tr>
          <tr>
            <th>{t("closed")}</th>
            <td>{<UtcTime value={o.close_time} />}</td>
          </tr>
          <tr>
            <th>{t("lastCollected")}</th>
            <td>{<UtcTime value={o.captured_at} />}</td>
          </tr>
        </tbody>
      </table>
      </TableScroll>

      <h3>{t("entities")}</h3>
      <div className="detail-grid">
        <List label={t("usernames")} values={o.usernames ?? []} empty={t("noneRecorded")} />
        <List label={t("sourceAddresses")} values={o.source_addresses ?? []} empty={t("noneRecorded")} />
        <List label={t("destinationAddresses")} values={o.local_destination_addresses ?? []} empty={t("noneRecorded")} />
        <List label={t("categories")} values={o.categories ?? []} empty={t("noneRecorded")} />
        <List label={t("ruleIds")} values={o.rule_ids ?? []} empty={t("noneRecorded")} />
        <List label={t("logSourceIds")} values={o.log_source_ids ?? []} empty={t("noneRecorded")} />
      </div>

      <h3>{t("history")}</h3>
      {history === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("historyFailedTitle")}</strong>
          </p>
          <p>
            {t("historyFailedHelp")}
          </p>
        </div>
      ) : history.length === 0 ? (
        <div className="notice">
          {t("historyEmpty")}
        </div>
      ) : (
        <TableScroll label={t("historyLabel")}>
        <table aria-label={t("historyLabel")}>
          <thead>
            <tr>
              <th>{t("captured")}</th>
              <th>{t("status")}</th>
              <th className="num">{t("magnitude")}</th>
              <th className="num">{t("severity")}</th>
              <th className="num">{t("events")}</th>
              <th>{t("assigned")}</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.captured_at}>
                <td className="nowrap">{<UtcTime value={h.captured_at} />}</td>
                <td>{enumLabel(say, "offenseStatus", h.status)}</td>
                <td className="num">{h.magnitude ?? "—"}</td>
                <td className="num">{h.severity ?? "—"}</td>
                <td className="num">{count(h.event_count, locale)}</td>
                <td>{h.assigned_to ?? <span className="muted">{t("unassigned")}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </>
  );
}
