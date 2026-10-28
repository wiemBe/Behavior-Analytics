"use client";

// One table per console: one row per family and scope (HISTORY-BACKFILL §B.10.3). Numbers are
// right-aligned in tabular figures, times carry their zone, and a missing measurement is an em
// dash with the reason beside it, never a zero.

import { useTranslations } from "next-intl";

import type { HistoryConsole, HistoryScopeSource } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";

import { familyName, formatCount, formatDays, relativeAge } from "./historyFormat";
import { rowsOf, type HistoryRow } from "./historyRows";
import { ageWords } from "./historyText";

import "@/components/ui/controls.css";
import "./history.css";

const MAX_GAPS_SHOWN = 3;
const MAX_SOURCES_SHOWN = 5;

function Time({ iso, now }: { iso: string | null; now: Date }) {
  const i18n = useBehaviorI18n();
  if (iso === null) return <span className="muted">—</span>;
  return (
    <UtcTime
      value={iso}
      variant="compact"
      wrapZone
      age={relativeAge(iso, now, ageWords(i18n))}
      ageClassName="cell-sub"
    />
  );
}

function Asked({ row }: { row: HistoryRow }) {
  const t = useTranslations("history.table");
  const { locale } = useBehaviorI18n();
  if (row.asked.days === null) return <span className="muted">—</span>;
  if (row.asked.of !== null && row.asked.of !== row.asked.days) {
    return (
      <>
        {plainCount(row.asked.days, locale)}
        <span className="cell-sub">{t("ofAsked", { count: plainCount(row.asked.of, locale) })}</span>
      </>
    );
  }
  return <>{plainCount(row.asked.days, locale)}</>;
}

function Proved({ row }: { row: HistoryRow }) {
  const t = useTranslations("history.table");
  if (row.proved.kind === "proved") {
    if (row.proved.floor === null) return <span className="muted">—</span>;
    return (
      <>
        <span className="cell-sub hb-lead">{t("windowProved")}</span>
        <UtcTime value={row.proved.floor} variant="compact" wrapZone />
      </>
    );
  }
  if (row.proved.kind === "per-source") {
    return (
      <span className="muted" title={t("perSourceTitle")}>
        {t("perSource")}
      </span>
    );
  }
  return <span className="muted">—</span>;
}

function Read({ row }: { row: HistoryRow }) {
  const t = useTranslations("history.table");
  const { locale } = useBehaviorI18n();
  const { read, stateCode } = row;
  if (read.unreadable) {
    return <span className="muted">{t("unreadable")}</span>;
  }
  if (read.days === null && read.buckets === null) return <span className="muted">—</span>;
  const days = read.days === null ? null : formatDays(read.days, locale);
  const target = stateCode === "READING" ? read.target : null;
  return (
    <>
      {days === null ? (
        <span className="muted">—</span>
      ) : target !== null ? (
        t("daysOf", { days, target: plainCount(target, locale) })
      ) : (
        t("daysRead", { days })
      )}
      {read.buckets !== null && (
        <span className="cell-sub">
          {t("buckets", { count: read.buckets, shown: formatCount(read.buckets, locale) })}
        </span>
      )}
    </>
  );
}

function SourceShortfall({
  label,
  sources,
  count,
}: {
  label: string;
  sources: HistoryScopeSource[] | null;
  count: number | null;
}) {
  const t = useTranslations("history.table");
  const { locale } = useBehaviorI18n();
  if (count === null || count <= 0) return null;
  const shown = (sources ?? []).slice(0, Math.min(MAX_SOURCES_SHOWN, count));
  const more = count - shown.length;
  const names = shown
    .map((source) => source.name ?? t("sourceKey", { key: source.key }))
    .join(", ");
  const remainder =
    more > 0
      ? `${shown.length > 0 ? " " : ""}${t(shown.length > 0 ? "andMore" : "more", { more: plainCount(more, locale) })}`
      : "";
  return (
    <div>
      <span className="muted">{label}: </span>
      {names}
      {remainder}.
    </div>
  );
}

function Reason({ row }: { row: HistoryRow }) {
  const t = useTranslations("history.table");
  const { locale } = useBehaviorI18n();
  const shown = row.gaps.slice(0, MAX_GAPS_SHOWN);
  const more = row.gaps.length - shown.length;
  const hasLastError = row.lastErrorClass !== null;
  const hasCutSources = row.sourcesCutCount !== null && row.sourcesCutCount > 0;
  const hasSilentSources = row.sourcesSilentCount !== null && row.sourcesSilentCount > 0;
  if (
    row.reason === null &&
    shown.length === 0 &&
    !hasLastError &&
    !hasCutSources &&
    !hasSilentSources
  ) {
    return <span className="muted">—</span>;
  }
  return (
    <>
      {row.reason}
      {shown.length > 0 && (
        <ul className="hb-gaps" aria-label={t("gaps")}>
          {shown.map((gap) => (
            <li key={`${gap.start}-${gap.end}`}>
              <span title={t("gapTitle", { start: utcTimeText(gap.start), end: utcTimeText(gap.end) })}>
                {t.rich("gapRange", {
                  start: () => <UtcTime value={gap.start} variant="compact" />,
                  end: () => <UtcTime value={gap.end} variant="compact" />,
                })}
              </span>
              <span className="cell-sub">{gap.reason}</span>
            </li>
          ))}
          {more > 0 && <li className="cell-sub">{t("andMore", { more: plainCount(more, locale) })}</li>}
        </ul>
      )}
      {hasLastError && (
        <span className="cell-sub hb-last-error warn">
          {t.rich("lastError", {
            errorClass: row.lastErrorClass ?? "",
            time: () => <UtcTime value={row.lastErrorAt} variant="compact" />,
          })}
        </span>
      )}
      <SourceShortfall
        label={t("cutLabel")}
        sources={row.sourcesCut}
        count={row.sourcesCutCount}
      />
      <SourceShortfall
        label={t("silentLabel")}
        sources={row.sourcesSilent}
        count={row.sourcesSilentCount}
      />
    </>
  );
}

function StateCell({ row }: { row: HistoryRow }) {
  return (
    <span className={`pill pill-quiet ${row.state.tone === "neutral" ? "" : row.state.tone}`}>
      {row.state.label}
    </span>
  );
}

export function HistoryTable({ console, now }: { console: HistoryConsole; now: Date }) {
  const t = useTranslations("history.table");
  const i18n = useBehaviorI18n();
  const rows = rowsOf(console, i18n);
  return (
    <TableScroll label={t("label", { name: console.name })}>
      <table className="table-dense hb-table">
        <caption className="sr-only">{t("caption", { name: console.name })}</caption>
        <thead>
          <tr>
            <th scope="col">{t("family")}</th>
            <th scope="col">{t("scope")}</th>
            <th scope="col">{t("state")}</th>
            <th scope="col" className="num">
              {t("asked")}
              <span className="cell-sub">{t("days")}</span>
            </th>
            <th scope="col">
              {t("proved")}
              <span className="cell-sub">{t("floor")}</span>
            </th>
            <th scope="col" className="num">
              {t("read")}
            </th>
            <th scope="col" className="num">
              {t("searches")}
            </th>
            <th scope="col" className="num">
              {t("through")}
              <span className="cell-sub">{t("utc")}</span>
            </th>
          </tr>
        </thead>
        {rows.map((row) => (
          <tbody key={row.key}>
            <tr className="hb-metrics">
              <th scope="row" className="hb-family">
                {familyName(row.family, i18n)}
              </th>
              <td className="cell-name">
                {row.scope}
                {row.scopeSub && <span className="cell-sub">{row.scopeSub}</span>}
              </td>
              <td>
                <StateCell row={row} />
              </td>
              <td className="num">
                <Asked row={row} />
              </td>
              <td>
                <Proved row={row} />
              </td>
              <td className="num hb-read">
                <Read row={row} />
              </td>
              <td className="num">
                {row.searches === null ? (
                  <span className="muted">—</span>
                ) : (
                  formatCount(row.searches, i18n.locale)
                )}
              </td>
              <td className="num hb-when">
                {row.last === null ? (
                  <span className="muted">—</span>
                ) : (
                  <>
                    {row.last.through === null ? (
                      <span className="muted">—</span>
                    ) : (
                      <Time iso={row.last.through} now={now} />
                    )}
                    {row.last.finished !== null && (
                      <span className="cell-sub">
                        {t.rich("finished", {
                          time: () => <UtcTime value={row.last?.finished ?? null} variant="compact" wrapZone />,
                        })}
                      </span>
                    )}
                  </>
                )}
              </td>
            </tr>
            <tr className="hb-detail">
              <td colSpan={8}>
                <dl className="hb-detail-list">
                  <div>
                    <dt>{t("reason")}</dt>
                    <dd>
                      <Reason row={row} />
                    </dd>
                  </div>
                  <div>
                    <dt>{t("laterYes")}</dt>
                    <dd>
                      {row.laterYes === null ? <span className="muted">—</span> : row.laterYes}
                    </dd>
                  </div>
                </dl>
              </td>
            </tr>
          </tbody>
        ))}
      </table>
    </TableScroll>
  );
}
