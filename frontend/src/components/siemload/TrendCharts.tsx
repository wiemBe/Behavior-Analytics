"use client";

// The stored QRadar health over 24 hours or 7 days, for one console: reachability and API latency,
// the fullest mount per host, the rule-engine queue per host and the throttle spill per host. A
// chart is drawn only from points that were stored; a series with none says so, with the reason
// the API gave, instead of drawing a flat line.

import { CustomChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useTranslations } from "next-intl";
import { useMemo, type ReactNode } from "react";

import { useEChart } from "@/components/home/useEChart";
import { UtcTime } from "@/components/ui/UtcTime";
import type { QRadarConsoleHistory, QRadarHealthHistory } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { HomePalette } from "@/lib/home/chartPalette";

import {
  gapLimitSeconds,
  hostLines,
  isFolded,
  latencyLine,
  reachabilitySegments,
  type LineGroup,
} from "./qradarDetail";
import { formatBytes, formatPercentFigure } from "./siemFormat";
import {
  buildLatencyOption,
  buildLinesOption,
  buildReachabilityOption,
  withTrendColours,
  type ValueKind,
} from "./trendOptions";

import "@/components/home/charts.css";

echarts.use([LineChart, CustomChart, GridComponent, TooltipComponent, CanvasRenderer]);

type Translate = ReturnType<typeof useTranslations>;

function Chart({
  build,
  label,
  className,
}: {
  build: ((p: HomePalette) => import("echarts").EChartsOption) | null;
  label: string;
  className: string;
}) {
  const ref = useEChart(build);
  return <div ref={ref} className={className} role="img" aria-label={label} />;
}

const FORMAT: Record<ValueKind, (value: number) => string> = {
  percent: formatPercentFigure,
  count: (value) => formatCount(Math.round(value)),
  bytes: formatBytes,
  ms: (value) => `${formatCount(value)} ms`,
};

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sl-chart-panel" aria-label={title}>
      <h4 className="sl-chart-title">{title}</h4>
      {children}
    </section>
  );
}

function NoSeries({ state, reason }: { state: string; reason: string }) {
  const t = useTranslations("siemLoad.detail.trend");
  const word = useTranslations("siemLoad.detail.health.state");
  return (
    <div className="notice" role="status">
      <p>
        <span className="sl-gap">
          <span className={`pill${state === "FAILED" ? " crit" : ""}`}>
            {word(state === "FAILED" ? "FAILED" : "NOT_COLLECTED")}
          </span>
          <span className="sl-reason">{reason || t("none")}</span>
        </span>
      </p>
    </div>
  );
}

type Why = { state: string; reason: string };

/** Why a group draws no line: the console's metric part, else the first host's own series. */
function whyEmpty(group: LineGroup, metricsReason: Why | null): Why | null {
  if (group.lines.length > 0) return null;
  return {
    state: metricsReason?.state ?? group.unread[0]?.state ?? "NOT_COLLECTED",
    reason: metricsReason?.reason ?? group.unread[0]?.reason ?? "",
  };
}

function LinesPanel({
  title,
  axis,
  aria,
  kind,
  group,
  range,
  scale,
  tip,
  yMax,
  metricsReason,
}: {
  title: string;
  axis: string;
  aria: string;
  kind: ValueKind;
  group: LineGroup;
  range: { since: number; until: number };
  scale: (value: number) => string;
  tip: (t: Translate, host: string, value: number, mount: string | null) => string;
  yMax?: number;
  metricsReason: Why | null;
}) {
  const t = useTranslations("siemLoad.detail.trend");
  const state = useTranslations("siemLoad.detail.health.state");
  const build = useMemo(
    () =>
      group.lines.length === 0
        ? null
        : (p: HomePalette) =>
            buildLinesOption(
              group.lines,
              kind,
              {
                xAxis: t("timeAxis"),
                yAxis: axis,
                tipValue: (host, value, mount) => tip(t, host, value, mount),
              },
              withTrendColours(p),
              range,
              yMax,
            ),
    [group.lines, kind, axis, t, tip, range, yMax],
  );
  const unread = group.unread.filter((host) => host.reason !== "" || host.state !== "OK");
  return (
    <Panel title={title}>
      {group.lines.length === 0 ? (
        <NoSeries
          state={whyEmpty(group, metricsReason)?.state ?? "NOT_COLLECTED"}
          reason={whyEmpty(group, metricsReason)?.reason ?? ""}
        />
      ) : (
        <>
          <Chart build={build} label={aria} className="hc-chart sl-chart" />
          <ul className="hc-key-list sl-key" aria-label={title}>
            {group.lines.map((line, at) => (
              <li key={line.host}>
                <span className={`sl-swatch sl-swatch-${at + 1}`} aria-hidden="true" />
                <span className="sl-host">{line.host}</span>
                <span className="sl-muted num">
                  {t("peak", { value: scale(line.peak) })} · {t("latest", { value: scale(line.last) })}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {group.omitted.length > 0 ? (
        <p className="sl-hint">
          {t("omitted", { count: group.omitted.length, max: 5, hosts: group.omitted.join(", ") })}
        </p>
      ) : null}
      {group.lines.length > 0 && unread.length > 0 ? (
        <ul className="sl-reasons">
          {unread.map((host) => (
            <li key={host.host}>
              {t("unreadHost", {
                host: host.host,
                state: state(host.state === "FAILED" ? "FAILED" : "NOT_COLLECTED"),
                reason: host.reason,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

function ReachabilityPanel({
  history,
  console_,
  range,
  limit,
}: {
  history: QRadarHealthHistory;
  console_: QRadarConsoleHistory;
  range: { since: number; until: number };
  limit: number;
}) {
  const t = useTranslations("siemLoad.detail.trend");
  const { reachability } = console_;
  const points = reachability.points;
  const segments = useMemo(
    () => reachabilitySegments(points, history.expected_interval_seconds, limit),
    [points, history.expected_interval_seconds, limit],
  );
  const latency = useMemo(() => latencyLine(points, limit), [points, limit]);
  const measured = points.filter((p) => p.api_latency_ms !== null);
  const up = points.filter((p) => p.reachable === true).length;
  const buildStrip = useMemo(
    () =>
      points.length === 0
        ? null
        : (p: HomePalette) =>
            buildReachabilityOption(
              segments,
              {
                xAxis: t("timeAxis"),
                up: t("up"),
                down: t("down"),
                unknown: t("unknown"),
              },
              withTrendColours(p),
              range,
            ),
    [points.length, segments, t, range],
  );
  const buildLatency = useMemo(
    () =>
      measured.length === 0
        ? null
        : (p: HomePalette) =>
            buildLatencyOption(
              latency,
              {
                xAxis: t("timeAxis"),
                yAxis: t("latencyAxis"),
                tipValue: (_host, value) => t("latencyTip", { value: formatCount(value) }),
              },
              withTrendColours(p),
              range,
            ),
    [measured.length, latency, t, range],
  );
  return (
    <Panel title={t("reachTitle")}>
      {points.length === 0 ? (
        <NoSeries state={reachability.state} reason={reachability.reason ?? t("reachNone")} />
      ) : (
        <>
          <Chart build={buildStrip} label={t("reachAria")} className="sl-strip" />
          <ul className="hc-key-list sl-key" aria-label={t("reachTitle")}>
            <li>
              <span className="sl-swatch sl-swatch-up" aria-hidden="true" />
              {t("up")}
            </li>
            <li>
              <span className="sl-swatch sl-swatch-down" aria-hidden="true" />
              {t("down")}
            </li>
            <li className="sl-muted num">{t("reachSummary", { up, total: points.length })}</li>
          </ul>
          {buildLatency ? (
            <Chart build={buildLatency} label={t("latencyAria")} className="hc-chart sl-chart" />
          ) : (
            <p className="sl-hint">{t("latencyNone")}</p>
          )}
        </>
      )}
    </Panel>
  );
}

export function TrendCharts({
  history,
  console_,
}: {
  history: QRadarHealthHistory;
  console_: QRadarConsoleHistory | undefined;
}) {
  const t = useTranslations("siemLoad.detail.trend");
  const range = useMemo(
    () => ({ since: Date.parse(history.since), until: Date.parse(history.until) }),
    [history.since, history.until],
  );
  const rangeSeconds = (range.until - range.since) / 1000;
  const folded = useMemo(
    () =>
      console_
        ? isFolded([
            ...console_.hosts.flatMap((h) => [
              h.disk_fullest_fraction,
              h.cre_queue,
              h.throttle_spill_bytes,
            ]),
          ]) || console_.reachability.points.length >= 400
        : false,
    [console_],
  );
  const limit = gapLimitSeconds(history.expected_interval_seconds, rangeSeconds, folded);
  const groups = useMemo(
    () =>
      console_
        ? {
            disk: hostLines(console_, (h) => h.disk_fullest_fraction, 100, limit),
            cre: hostLines(console_, (h) => h.cre_queue, 1, limit),
            spill: hostLines(console_, (h) => h.throttle_spill_bytes, 1, limit),
          }
        : null,
    [console_, limit],
  );
  const tips = useMemo(
    () => ({
      disk: (tr: Translate, host: string, value: number, mount: string | null) =>
        mount
          ? tr("diskTip", { host, value: value.toFixed(1), mount })
          : tr("diskTipNoMount", { host, value: value.toFixed(1) }),
      cre: (tr: Translate, host: string, value: number) =>
        tr("creTip", { host, value: formatCount(Math.round(value)) }),
      spill: (tr: Translate, host: string, value: number) =>
        tr("spillTip", { host, value: formatBytes(value) }),
    }),
    [],
  );
  if (!console_ || !groups) {
    return (
      <div className="notice" role="status">
        <p>{t("noConsole")}</p>
      </div>
    );
  }
  const metricsReason =
    console_.metrics.state === "OK" || console_.metrics.state === "PARTIAL"
      ? null
      : { state: console_.metrics.state, reason: console_.metrics.reason ?? "" };
  // Three charts that hold nothing for the same reason say it once.
  const whys = [groups.disk, groups.cre, groups.spill].map((g) => whyEmpty(g, metricsReason));
  const first = whys[0];
  const sharedWhy =
    first && whys.every((w) => w && w.state === first.state && w.reason === first.reason)
      ? first
      : null;
  return (
    <>
      <p className="sl-asof">
        {t.rich("window", {
          from: () => <UtcTime value={history.since} />,
          to: () => <UtcTime value={history.until} />,
        })}
      </p>
      <div className="sl-charts">
        <ReachabilityPanel history={history} console_={console_} range={range} limit={limit} />
        {sharedWhy ? (
          <Panel title={t("metricsTitle")}>
            <NoSeries state={sharedWhy.state} reason={sharedWhy.reason} />
          </Panel>
        ) : (
          <>
        <LinesPanel
          title={t("diskTitle")}
          axis={t("diskAxis")}
          aria={t("diskAria")}
          kind="percent"
          group={groups.disk}
          range={range}
          scale={formatPercentFigure}
          tip={tips.disk}
          yMax={100}
          metricsReason={metricsReason}
        />
        <LinesPanel
          title={t("creTitle")}
          axis={t("creAxis")}
          aria={t("creAria")}
          kind="count"
          group={groups.cre}
          range={range}
          scale={FORMAT.count}
          tip={tips.cre}
          metricsReason={metricsReason}
        />
        <LinesPanel
          title={t("spillTitle")}
          axis={t("spillAxis")}
          aria={t("spillAria")}
          kind="bytes"
          group={groups.spill}
          range={range}
          scale={FORMAT.bytes}
          tip={tips.spill}
          metricsReason={metricsReason}
        />
          </>
        )}
      </div>
    </>
  );
}
