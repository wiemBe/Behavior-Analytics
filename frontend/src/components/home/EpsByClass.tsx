"use client";

// EPS by source class (UR.4.3): a donut of at most five slices and, beside it, the list
// that carries the same figures as text. The donut is a picture of the list and is hidden
// from assistive technology; the list is a table with the total in its footer.
//
// A class with no complete bucket is counted in words, never added as zero. When the
// fifth entry is "Other classes", the list names the classes it holds.

import { PieChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { HISTORY_TEXT } from "@/components/behavior/historyOrigin";
import { homeReads } from "@/lib/api";
import { NOT_MEASURED } from "@/lib/behavior";
import { formatPercent } from "@/lib/format";
import { fleetDrillHref, formatEpsValue, type FleetWindow } from "@/lib/fleet";
import type { HomePalette } from "@/lib/home/chartPalette";
import { sliceVar } from "@/lib/home/chartPalette";
import { epsByClass, REST_KEY, type ClassEps, type EpsByClass as Model } from "@/lib/home/epsByClass";
import { badgeText } from "@/lib/home/qualifiers";
import { RANGE_TEXT, useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { buildClassOption } from "./chartOptions";
import { useEChart } from "./useEChart";
import { WidgetFrame } from "./WidgetFrame";

import "./charts.css";

echarts.use([PieChart, TooltipComponent, CanvasRenderer]);

/** An aggregate has no class to filter by; it goes to the fleet's by-class table. */
export function classHref(e: Pick<ClassEps, "key">, window: FleetWindow): string {
  return e.key === REST_KEY
    ? fleetDrillHref(window)
    : `/fleet/sources?class=${encodeURIComponent(e.key)}`;
}

const share = (v: number | null) => formatPercent(v, null, 0);
const eps = (v: number | null | undefined) => formatEpsValue(v) ?? NOT_MEASURED;

function reasonOf(model: Model): string {
  const q = model.qualifiers.find((x) => x.kind === "INSUFFICIENT_DATA");
  return q && q.kind === "INSUFFICIENT_DATA" ? q.reason : "No class has a mean EPS.";
}

function Donut({ model, window }: { model: Model; window: FleetWindow }) {
  const router = useRouter();
  const option = useMemo(
    () => (p: HomePalette) => buildClassOption(model.entries, p),
    [model.entries],
  );
  const ref = useEChart(option, (click) => {
    const entry = click.dataIndex != null ? model.entries[click.dataIndex] : undefined;
    if (entry) router.push(classHref(entry, window));
  });
  return (
    <div className="hc-donut" aria-hidden="true">
      <div ref={ref} className="hc-donut-canvas" />
      <div className="hc-donut-total">
        <span className="hc-donut-num">{eps(model.totalEps)}</span>
        <span className="hc-donut-unit">EPS</span>
      </div>
    </div>
  );
}

function List({ model, window }: { model: Model; window: FleetWindow }) {
  return (
    <table className="hc-list">
      <caption className="sr-only">
        Mean EPS by source class, complete intervals only. Total {eps(model.totalEps)} EPS.
      </caption>
      <thead>
        <tr>
          <th scope="col">Class</th>
          <th scope="col" className="num">
            EPS
          </th>
          <th scope="col" className="num">
            Share
          </th>
        </tr>
      </thead>
      <tbody>
        {model.entries.map((e, i) => (
          <tr key={e.key}>
            <th scope="row">
              <svg className="hc-swatch" viewBox="0 0 1 1" aria-hidden="true" focusable="false">
                <rect width="1" height="1" fill={sliceVar(i)} />
              </svg>
              <Link href={classHref(e, window)}>{e.label}</Link>
              {e.key === REST_KEY && model.restContains.length > 0 && (
                <span className="hc-rest" title={model.restText ?? undefined}>
                  {model.restContains.join(", ")}
                </span>
              )}
            </th>
            <td className="num">{eps(e.eps)}</td>
            <td className="num">{share(e.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EpsByClass({ range }: RangedProps = {}) {
  const window = useEffectiveRange(range);
  const read = homeReads.fleetSummary(window);
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? epsByClass(data) : null), [data]);
  const complete = data?.classes.reduce((sum, c) => sum + c.buckets.complete, 0) ?? 0;
  const fromHistory =
    data?.classes.reduce((sum, c) => sum + c.buckets.complete_from_history, 0) ?? 0;

  return (
    <WidgetFrame
      id="home-eps-class"
      title="EPS by source class"
      windowText={RANGE_TEXT[window]}
      badge={(model && badgeText(model.qualifiers)) || undefined}
      link={{ href: fleetDrillHref(window), label: "Fleet" }}
      state={state}
      what="EPS by class"
      permission="read:fleet"
    >
      {() =>
          model && (
            <>
              {fromHistory > 0 && (
                <p className="tw-note">
                  {fromHistory.toLocaleString("en-US")} of {complete.toLocaleString("en-US")} complete
                  buckets were {HISTORY_TEXT}.
                </p>
              )}
              {model.entries.length === 0 ? (
                <p className="tw-state">{reasonOf(model)}</p>
              ) : (
                <div className="hc-class">
                  <Donut model={model} window={window} />
                  <List model={model} window={window} />
                </div>
              )}
              {model.classesWithoutData > 0 && (
                <p className="tw-note">
                  {model.classesWithoutData} class{model.classesWithoutData === 1 ? " has" : "es have"}{" "}
                  no complete bucket and {model.classesWithoutData === 1 ? "is" : "are"} not in the
                  total.
                </p>
              )}
            </>
          )
      }
    </WidgetFrame>
  );
}
