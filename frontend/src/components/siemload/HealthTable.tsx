"use client";

// Disk, system and pipeline per host, from QRadar's own Health Metrics as the platform stored
// them. A cell that holds a figure shows it with its unit; a cell that does not says so in words
// and never shows a zero. The window is stated once above the table. A server of the managed host
// list that sent no Health Metrics is a row that says so, and a host that sent Health Metrics but
// is not in that list is listed beneath, never dropped.

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type {
  QRadarConsoleLatest,
  QRadarHostMetrics,
  QRadarMetricCell,
  QRadarMount,
} from "@/lib/api";
import { formatCount } from "@/lib/format";
import { formatExactDuration } from "@/lib/time";

import { HostName } from "./HostName";
import { cellHasValue, distinctReasons, type HealthJoin, type MemberRow } from "./qradarDetail";
import { formatBytes, formatPercentFigure } from "./siemFormat";

const COLUMNS = 7;

/** The word for a figure that was not read; the reason is the title and the note under the table. */
function NotCollected({ failed = false, reason }: { failed?: boolean; reason?: string | null }) {
  const t = useTranslations("siemLoad.detail.health.state");
  return (
    <span className="sl-na" title={reason ?? undefined}>
      {t(failed ? "FAILED" : "NOT_COLLECTED")}
    </span>
  );
}

/** A figure that was read, or the word for why it was not. `lowerBound` marks a partial window. */
function Figure({
  cell,
  render,
  lowerBound,
}: {
  cell: QRadarMetricCell;
  render: (value: number) => ReactNode;
  lowerBound: boolean;
}) {
  if (!cellHasValue(cell)) return <NotCollected failed={cell.state === "FAILED"} reason={cell.reason} />;
  return (
    <span className={cell.state === "STALE" ? "sl-stale-figure" : undefined}>
      {lowerBound ? "≥ " : null}
      {render(cell.value)}
    </span>
  );
}

function LevelPill({ level }: { level: QRadarMount["level"] }) {
  const t = useTranslations("siemLoad.detail.health");
  if (level === "FAIL") return <span className="pill crit">{t("levelFail")}</span>;
  if (level === "WARN") return <span className="pill warn">{t("levelWarn")}</span>;
  return null;
}

function DiskCell({ disk, lowerBound }: { disk: QRadarHostMetrics["disk"]; lowerBound: boolean }) {
  const t = useTranslations("siemLoad.detail.health");
  const { fullest } = disk;
  if (disk.state === "NOT_COLLECTED" || disk.state === "FAILED" || !fullest) {
    return <NotCollected failed={disk.state === "FAILED"} reason={disk.reason} />;
  }
  const others = disk.mounts.filter((mount) => mount.mount !== fullest.mount);
  return (
    <>
      <span className={disk.state === "STALE" ? "sl-stale-figure" : undefined}>
        {lowerBound ? "≥ " : null}
        {formatPercentFigure(fullest.used_percent)} <span className="sl-mount">{fullest.mount}</span>
      </span>{" "}
      <LevelPill level={fullest.level} />
      {fullest.free_bytes !== null ? (
        <span className="cell-sub">{t("free", { size: formatBytes(fullest.free_bytes) })}</span>
      ) : null}
      {fullest.free_bytes !== null && fullest.total_from !== null ? (
        <span className="cell-sub">{t.rich("totalAsOf", { time: () => <UtcTime value={fullest.total_from} variant="compact" /> })}</span>
      ) : null}
      {others.length > 0 ? (
        <details className="sl-mounts">
          <summary>{t("mounts", { count: disk.mounts.length })}</summary>
          <ul>
            {disk.mounts.map((mount) => (
              <li key={mount.mount}>
                <span className="sl-mount">{mount.mount}</span>{" "}
                <span className="num">{formatPercentFigure(mount.used_percent)}</span>
                {mount.free_bytes !== null ? (
                  <span className="sl-muted"> · {t("free", { size: formatBytes(mount.free_bytes) })}</span>
                ) : null}
                {mount.free_bytes !== null && mount.total_from !== null ? (
                  <span className="sl-muted">
                    {" · "}
                    {t.rich("totalAsOf", { time: () => <UtcTime value={mount.total_from} variant="compact" /> })}
                  </span>
                ) : null}{" "}
                <LevelPill level={mount.level} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function MetricsCells({ metrics, lowerBound }: { metrics: QRadarHostMetrics; lowerBound: boolean }) {
  const t = useTranslations("siemLoad.detail.health");
  const { system, pipeline } = metrics;
  const spill = pipeline.throttle_spill_bytes;
  const stage = pipeline.busiest_stage;
  return (
    <>
      <td>
        <DiskCell disk={metrics.disk} lowerBound={lowerBound} />
      </td>
      <td className="num">
        <Figure cell={system.cpu_percent} render={formatPercentFigure} lowerBound={lowerBound} />
      </td>
      <td className="num">
        <Figure cell={system.memory_percent} render={formatPercentFigure} lowerBound={lowerBound} />
      </td>
      <td className="num">
        <Figure
          cell={pipeline.cre_queue}
          render={(value) => formatCount(value)}
          lowerBound={lowerBound}
        />
      </td>
      <td className="num">
        <Figure cell={spill} render={formatBytes} lowerBound={lowerBound} />
        {pipeline.throttle_spilling === true ? (
          <span className="cell-sub">
            <span className="pill warn">{t("spilling")}</span>
          </span>
        ) : null}
      </td>
      <td className="num">
        {stage ? (
          <>
            {lowerBound ? "≥ " : null}
            {formatPercentFigure(stage.busy_percent)}
            <span className="cell-sub">{stage.stage}</span>
          </>
        ) : (
          <NotCollected />
        )}
      </td>
    </>
  );
}

function Row({ member, lowerBound }: { member: MemberRow; lowerBound: boolean }) {
  const t = useTranslations("siemLoad.detail.health");
  const hosts = useTranslations("siemLoad.detail.hosts");
  const state = useTranslations("siemLoad.detail.health.state");
  return (
    <tr>
      <th scope="row">
        {member.hostname === null ? <span className="sl-host">—</span> : <HostName host={member.hostname} />}
        {member.role ? (
          <span className="cell-sub">
            {member.role === "primary" ? hosts("memberPrimary") : hosts("memberSecondary")}
          </span>
        ) : null}
      </th>
      {member.metrics ? (
        <MetricsCells metrics={member.metrics} lowerBound={lowerBound} />
      ) : (
        <td colSpan={COLUMNS - 1}>
          <span className="sl-gap">
            <span className="pill">{state("NOT_COLLECTED")}</span>
            <span className="sl-reason">{t("noMetricsReason")}</span>
          </span>
        </td>
      )}
    </tr>
  );
}

function OrphanRow({ metrics, lowerBound }: { metrics: QRadarHostMetrics; lowerBound: boolean }) {
  return (
    <tr>
      <th scope="row">
        <HostName host={metrics.host} />
      </th>
      <MetricsCells metrics={metrics} lowerBound={lowerBound} />
    </tr>
  );
}

export function HealthTable({
  console_,
  join,
  hostsRead,
}: {
  console_: QRadarConsoleLatest;
  join: HealthJoin;
  /** False when the managed host list was not read: then nothing is matched to it. */
  hostsRead: boolean;
}) {
  const t = useTranslations("siemLoad.detail.health");
  const state = useTranslations("siemLoad.detail.health.state");
  const { metrics, window } = console_;

  if (metrics.state === "NOT_COLLECTED" || metrics.state === "FAILED" || window === null) {
    return (
      <div className="notice" role="status">
        <p>
          <span className="sl-gap">
            <span className={`pill${metrics.state === "FAILED" ? " crit" : ""}`}>
              {state(metrics.state === "FAILED" ? "FAILED" : "NOT_COLLECTED")}
            </span>
            <span className="sl-reason">{metrics.reason?.trim() || t("noMetricsReason")}</span>
          </span>
        </p>
        {console_.last_metrics_window_end ? (
          <p>
            {t.rich("windowEnded", { time: () => <UtcTime value={console_.last_metrics_window_end} /> })}
          </p>
        ) : null}
      </div>
    );
  }

  const lowerBound = metrics.state === "PARTIAL";
  const members = join.rows.flatMap((row) => row.members);
  const reasons = distinctReasons([
    ...console_.hosts.flatMap((host) => [
      host.disk,
      host.system.cpu_percent,
      host.system.memory_percent,
      host.pipeline.cre_queue,
      host.pipeline.throttle_spill_bytes,
    ]),
  ]);

  return (
    <>
      <p className="sl-asof">
        {t.rich("window", {
          from: () => <UtcTime value={window.start} />,
          to: () => <UtcTime value={window.end} />,
          cadence: formatExactDuration(window.cadence_seconds),
          collected: () => <UtcTime value={window.collected_at} />,
        })}
      </p>
      {metrics.state === "STALE" ? (
        <p className="banner warn" role="status">
          {t("stale", { reason: metrics.reason ?? "" })}
        </p>
      ) : null}
      {metrics.state === "PARTIAL" ? (
        <p className="banner warn" role="status">
          {t("partial", { reason: metrics.reason ?? "" })}
        </p>
      ) : null}
      {!hostsRead ? <p className="sl-hint">{t("hostsNotRead")}</p> : null}
      {members.length === 0 && join.orphans.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("noHosts")}</strong>
          </p>
        </div>
      ) : (
        <TableScroll label={t("label", { name: console_.instance_name })}>
          <table className="sl-table sl-health-table">
            <caption className="sr-only">{t("label", { name: console_.instance_name })}</caption>
            <thead>
              <tr>
                <th scope="col">{t("host")}</th>
                <th scope="col">{t("disk")}</th>
                <th scope="col" className="num">
                  {t("cpu")}
                </th>
                <th scope="col" className="num">
                  {t("memory")}
                </th>
                <th scope="col" className="num">
                  {t("creQueue")}
                </th>
                <th scope="col" className="num">
                  {t("spill")}
                </th>
                <th scope="col" className="num">
                  {t("stage")}
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <Row key={member.key} member={member} lowerBound={lowerBound} />
              ))}
            </tbody>
            {join.orphans.length > 0 ? (
              <tbody className="sl-orphans">
                <tr>
                  <th scope="rowgroup" colSpan={COLUMNS} className="sl-group-label">
                    {t("unmatchedHeading")}
                    <span className="sl-note">{t("unmatchedNote")}</span>
                  </th>
                </tr>
                {join.orphans.map((host) => (
                  <OrphanRow key={host.host} metrics={host} lowerBound={lowerBound} />
                ))}
              </tbody>
            ) : null}
          </table>
        </TableScroll>
      )}
      {reasons.length > 0 ? (
        <ul className="sl-reasons">
          {reasons.map((reason) => (
            <li key={reason}>{t("notCollectedLead", { reason })}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
