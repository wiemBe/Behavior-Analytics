"use client";

// The managed hosts of one console, one row per host. An HA pair is one row group: the host's own
// row (its EPS and FPM, counted once) and a row for each member with its role and status. The
// pair state is a word, and only a pair that needs a look takes a warning tone: the standby of a
// healthy pair is where it should be and is never called "not ACTIVE".

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import type { QRadarField, QRadarHostEntry, QRadarHosts, QRadarRate } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { FieldGap } from "./FieldValue";
import { isExpectedStandby, memberStatus, pairStatuses, pairTone, type MemberRow } from "./qradarDetail";
import type { HostRow } from "./qradarDetail";

const MISSING = "—";
/** Columns: host, role and appliance, status, and two rate blocks of two columns each. */
const COLUMNS = 7;

const PAIR_WORD = {
  ha_healthy: ["pairHealthy", "pairHealthySentence"],
  ha_degraded: ["pairDegraded", "pairDegradedSentence"],
  ha_failed_over: ["pairFailedOver", "pairFailedOverSentence"],
  ha_failed: ["pairFailed", "pairFailedSentence"],
  unknown: ["pairUnknown", "pairUnknownSentence"],
} as const;

function Rates({ field }: { field: QRadarField<QRadarRate> }) {
  const t = useTranslations("siemLoad.detail.hosts");
  if (field.state !== "OK" || !field.value) {
    return (
      <td colSpan={2}>
        <FieldGap field={field} />
      </td>
    );
  }
  const rate = field.value;
  return (
    <>
      <td className="num">
        <span className="sl-pairfig">{formatCount(rate.average)}</span>
        <span className="sl-pairfig">{formatCount(rate.peak)}</span>
      </td>
      <td className="num">
        {t("allocatedOf", {
          allocated: formatCount(rate.allocation),
          limit: formatCount(rate.hardware_limit),
        })}
      </td>
    </>
  );
}

function HostCell({ name, ip, note }: { name: string | null; ip: string | null; note?: ReactNode }) {
  return (
    <th scope="row">
      <span className="sl-host">{name ?? MISSING}</span>
      {ip ? <span className="cell-sub sl-id">{ip}</span> : null}
      {note}
    </th>
  );
}

function StatusText({ member, entry }: { member: MemberRow; entry: QRadarHostEntry }) {
  const t = useTranslations("siemLoad.detail.hosts");
  if (member.member === null) return <span className="sl-muted">{t("statusNone")}</span>;
  const status = memberStatus(member.member);
  if (status === null) return <FieldGap field={member.member.status} />;
  if (isExpectedStandby(entry, member.member)) {
    return (
      <>
        {status}
        <span className="cell-sub">{t("standbyExpected")}</span>
      </>
    );
  }
  // A single host that is not ACTIVE needs a look; a pair's states are judged by the pair.
  if (entry.pair_state === "single" && status !== "ACTIVE") {
    return <span className="pill warn">{status}</span>;
  }
  return <>{status}</>;
}

function PairState({ entry }: { entry: QRadarHostEntry }) {
  const t = useTranslations("siemLoad.detail.hosts");
  const unread = t("statusNone");
  if (entry.pair_state === "single") return null;
  const [word, sentence] = PAIR_WORD[entry.pair_state];
  const tone = pairTone(entry.pair_state);
  return (
    <>
      <span className={`pill${tone === "neutral" ? "" : ` ${tone}`}`}>{t(word)}</span>
      <span className="cell-sub sl-sentence">
        {t(sentence, { statuses: pairStatuses(entry, unread) })}
      </span>
    </>
  );
}

function roleLabel(entry: QRadarHostEntry, t: ReturnType<typeof useTranslations>) {
  if (entry.role === "console") return <>{t("roleConsole")}</>;
  if (entry.role === "managed") return <>{t("roleManaged")}</>;
  return <span title={t("roleUnknown")}>{MISSING}</span>;
}

function HostGroup({ row }: { row: HostRow }) {
  const t = useTranslations("siemLoad.detail.hosts");
  const { entry } = row;
  const pair = entry.pair_state !== "single";
  const only = row.members[0];
  const appliance = entry.appliance_description ?? entry.appliance_id;
  return (
    <tbody className={pair ? "sl-pair" : undefined}>
      <tr>
        <HostCell
          name={entry.hostname ?? only?.hostname ?? null}
          ip={entry.private_ip ?? only?.ip ?? null}
        />
        <td className="sl-appliance">
          {roleLabel(entry, t)}
          <span className="cell-sub">
            {appliance ?? MISSING} · {entry.version ?? MISSING}
          </span>
        </td>
        <td className="sl-status">
          {pair ? <PairState entry={entry} /> : only ? <StatusText member={only} entry={entry} /> : MISSING}
        </td>
        <Rates field={entry.eps} />
        <Rates field={entry.fpm} />
      </tr>
      {pair
        ? row.members.map((member) => (
            <tr key={member.key} className="sl-member">
              <HostCell
                name={member.hostname}
                ip={member.ip}
                note={
                  <span className="cell-sub">
                    {member.role === "primary"
                      ? t("memberPrimary")
                      : member.role === "secondary"
                        ? t("memberSecondary")
                        : t("memberUnknown")}
                  </span>
                }
              />
              <td />
              <td className="sl-status">
                <StatusText member={member} entry={entry} />
              </td>
              <td colSpan={COLUMNS - 3} />
            </tr>
          ))
        : null}
    </tbody>
  );
}

export function HostsTable({
  name,
  rows,
  hosts,
}: {
  name: string;
  rows: HostRow[];
  hosts: QRadarField<QRadarHosts> | undefined;
}) {
  const t = useTranslations("siemLoad.detail.hosts");
  if (hosts === undefined || hosts.state !== "OK" || !hosts.value) {
    return (
      <div className="notice" role="status">
        <p>
          {hosts ? (
            <FieldGap field={hosts} />
          ) : (
            <span className="sl-reason">{t("notSent")}</span>
          )}
        </p>
      </div>
    );
  }
  if (hosts.value.items.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("empty")}</strong>
        </p>
        <p>{t("emptyHelp")}</p>
      </div>
    );
  }
  return (
    <>
      {hosts.value.truncated ? (
        <p className="banner warn" role="status">
          {t("truncated", { count: hosts.value.hosts })}
        </p>
      ) : null}
      <TableScroll label={t("label", { name })}>
        <table className="sl-table sl-hosts-table">
          <caption className="sr-only">{t("label", { name })}</caption>
          <thead>
            <tr>
              <th scope="col" rowSpan={2}>
                {t("host")}
              </th>
              <th scope="col" rowSpan={2}>
                {t("roleAppliance")}
              </th>
              <th scope="col" rowSpan={2}>
                {t("status")}
              </th>
              <th scope="colgroup" colSpan={2} className="sl-group">
                {t("eps")}
              </th>
              <th scope="colgroup" colSpan={2} className="sl-group">
                {t("fpm")}
              </th>
            </tr>
            <tr>
              {[0, 1].flatMap((block) =>
                (["averagePeak", "allocatedLimit"] as const).map((key) => (
                  <th key={`${block}-${key}`} scope="col" className="num">
                    {t(key)}
                  </th>
                )),
              )}
            </tr>
          </thead>
          {rows.map((row) => (
            <HostGroup key={row.members[0]?.key ?? row.entry.hostname ?? "host"} row={row} />
          ))}
        </table>
      </TableScroll>
      <p className="sl-hint">{t("ratesNote")}</p>
    </>
  );
}
