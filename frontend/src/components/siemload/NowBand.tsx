"use client";

// The first answer of the page: how much are we loading QRadar now. Searches in flight, the permit
// slots held against the cap, and what the last 5, 15 and 60 minutes held. A part the backend could
// not read says so; it is never drawn as zero.

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { ArielInFlight, ArielLoadCurrent, ArielSlots, ArielWindow, ArielWindows } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { formatMs } from "./siemFormat";

function PartGap({ reason }: { reason: string }) {
  const state = useTranslations("siemLoad.state");
  return (
    <span className="sl-gap">
      <span className="pill">{state("notRead")}</span>
      <span className="sl-reason">{state("reason", { reason })}</span>
    </span>
  );
}

function InFlightFact({ part }: { part: ArielInFlight }) {
  const t = useTranslations("siemLoad.now");
  if (!part.available) return <PartGap reason={part.reason} />;
  return (
    <>
      <span className="sl-line num">
        {t("inFlightValue", { active: part.active.length })} ·{" "}
        {t("inFlightStale", { stale: part.stale.length })}
      </span>
      <span className="sl-note">{t("inFlightNote", { seconds: part.stale_after_seconds })}</span>
    </>
  );
}

function SlotsFact({ part }: { part: ArielSlots }) {
  const t = useTranslations("siemLoad.now");
  if (!part.available) return <PartGap reason={part.reason} />;
  if (!part.enforced) {
    return (
      <>
        <span className="sl-line">{t("slotsNotEnforced")}</span>
        <span className="sl-note">{t("slotsNotEnforcedNote")}</span>
      </>
    );
  }
  return (
    <>
      <span className="sl-line num">
        {t("slotsGlobal", { held: part.global_held, cap: part.global_cap })}
      </span>
      <span className="sl-note num">
        {t("slotsConsole", { held: part.console_pool_locks, cap: part.console_cap })}
      </span>
      <span className="sl-note num">{t("slotsReserved", { reserved: part.reserved_slots })}</span>
    </>
  );
}

const ROWS = [
  { key: "minutes_5", label: "last5" },
  { key: "minutes_15", label: "last15" },
  { key: "minutes_60", label: "last60" },
] as const;

function WindowRow({ label, window }: { label: ReactNode; window: ArielWindow }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num">{formatCount(window.submitted)}</td>
      <td className="num">{formatCount(window.completed)}</td>
      <td className="num">{formatCount(window.failed)}</td>
      <td className="num">{formatCount(window.refused)}</td>
      <td className="num">{formatMs(window.wall_ms_p50)}</td>
      <td className="num">{formatMs(window.wall_ms_p95)}</td>
    </tr>
  );
}

function WindowsTable({ part }: { part: ArielWindows }) {
  const t = useTranslations("siemLoad.now");
  if (!part.available) {
    return (
      <div className="notice" role="status">
        <PartGap reason={part.reason} />
      </div>
    );
  }
  return (
    <>
      <TableScroll label={t("windowsCaption")}>
        <table className="sl-table sl-table-compact">
          <caption className="sr-only">{t("windowsCaption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("window")}</th>
              <th scope="col" className="num">
                {t("submitted")}
              </th>
              <th scope="col" className="num">
                {t("completed")}
              </th>
              <th scope="col" className="num">
                {t("failed")}
              </th>
              <th scope="col" className="num">
                {t("refused")}
              </th>
              <th scope="col" className="num">
                {t("wallP50")}
              </th>
              <th scope="col" className="num">
                {t("wallP95")}
              </th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => (
              <WindowRow key={row.key} label={t(row.label)} window={part[row.key]} />
            ))}
          </tbody>
        </table>
      </TableScroll>
      {part.minutes_60.submitted === 0 ? <p className="sl-hint">{t("noneSent")}</p> : null}
      <p className="sl-hint">{t("windowsNote")}</p>
    </>
  );
}

export function NowBand({ current }: { current: ArielLoadCurrent }) {
  const t = useTranslations("siemLoad.now");
  return (
    <section className="sl-section" aria-labelledby="sl-now-h">
      <div className="sl-section-head">
        <h2 className="sl-h" id="sl-now-h">
          {t("heading")}
        </h2>
        <p className="sl-asof">
          {t.rich("asOf", { time: () => <UtcTime value={current.as_of} /> })}
        </p>
      </div>
      <dl className="sl-facts">
        <dt>{t("inFlight")}</dt>
        <dd>
          <InFlightFact part={current.in_flight} />
        </dd>
        <dt>{t("slots")}</dt>
        <dd>
          <SlotsFact part={current.slots} />
        </dd>
      </dl>
      <WindowsTable part={current.windows} />
    </section>
  );
}
