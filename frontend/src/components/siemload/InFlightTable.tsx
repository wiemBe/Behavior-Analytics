"use client";

// The searches this platform has sent and not seen finish. A stale one (open longer than the poll
// budget) is set apart and labelled: its worker probably stopped before it recorded an end, so it
// is no longer load.

import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { ArielInFlight, ArielInFlightEntry, QRadarHealthPart } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { asTextTranslator } from "@/i18n/textTranslator";
import { formatDuration, formatExactDuration } from "@/lib/time";

import { callerLabel } from "./callers";

function consoleNames(part: QRadarHealthPart | undefined): Map<string, string> {
  const names = new Map<string, string>();
  if (part?.available) {
    for (const health of part.consoles) names.set(health.instance_id, health.instance_name);
  }
  return names;
}

export function InFlightTable({
  part,
  qradar,
}: {
  part: ArielInFlight;
  qradar: QRadarHealthPart | undefined;
}) {
  const t = useTranslations("siemLoad.flight");
  const callers = asTextTranslator(useTranslations("siemLoad.caller"));
  const state = useTranslations("siemLoad.state");
  const parts = useTranslations("siemLoad.part");
  const names = consoleNames(qradar);

  const row = (entry: ArielInFlightEntry, stale: boolean, at: number) => (
    <tr key={`${stale ? "s" : "a"}-${at}`}>
      <td>{callerLabel(callers, entry.caller)}</td>
      <td className="cell-name">
        {entry.instance_id === null
          ? "—"
          : (names.get(entry.instance_id) ?? <span className="sl-id">{entry.instance_id}</span>)}
      </td>
      <td>
        <span className={stale ? "pill warn" : "pill"}>{stale ? t("stale") : t("active")}</span>
      </td>
      <td className="num">
        <UtcTime value={entry.submitted_at} variant="compact" bare />
      </td>
      <td className="num">{formatDuration(entry.age_seconds)}</td>
      <td className="num">{formatCount(entry.polls)}</td>
      <td className="num">{formatExactDuration(entry.range_seconds)}</td>
    </tr>
  );

  return (
    <section className="sl-section" aria-labelledby="sl-flight-h">
      <h2 className="sl-h" id="sl-flight-h">
        {t("heading")}
      </h2>
      {!part.available ? (
        <div className="notice" role="status">
          <p>
            <strong>{state("partUnavailable", { part: parts("inFlight") })}</strong>
          </p>
          {part.reason?.trim() ? <p>{state("reason", { reason: part.reason })}</p> : null}
        </div>
      ) : part.active.length === 0 && part.stale.length === 0 ? (
        <p className="sl-hint">{t("empty")}</p>
      ) : (
        <TableScroll label={t("label")}>
          <table className="sl-table">
            <caption className="sr-only">{t("label")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("caller")}</th>
                <th scope="col">{t("console")}</th>
                <th scope="col">{t("state")}</th>
                <th scope="col" className="num">
                  {t("submitted")}
                </th>
                <th scope="col" className="num">
                  {t("age")}
                </th>
                <th scope="col" className="num">
                  {t("polls")}
                </th>
                <th scope="col" className="num">
                  {t("range")}
                </th>
              </tr>
            </thead>
            {part.active.length > 0 ? (
              <tbody>{part.active.map((entry, at) => row(entry, false, at))}</tbody>
            ) : null}
            {part.stale.length > 0 ? (
              <tbody className="sl-stale">
                <tr>
                  <th scope="rowgroup" colSpan={7} className="sl-stale-label">
                    {t("staleGroup", { seconds: part.stale_after_seconds })}
                  </th>
                </tr>
                {part.stale.map((entry, at) => row(entry, true, at))}
              </tbody>
            ) : null}
          </table>
        </TableScroll>
      )}
    </section>
  );
}
