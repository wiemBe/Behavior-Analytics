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
import { homeReads } from "@/lib/api";
import { NOT_MEASURED } from "@/lib/behavior";
import type { HomePalette } from "@/lib/home/chartPalette";
import { sliceVar } from "@/lib/home/chartPalette";
import { epsByClass, REST_KEY, type ClassEps, type EpsByClass as Model } from "@/lib/home/epsByClass";
import { formatEps } from "@/lib/home/format";
import { badgeText } from "@/lib/home/qualifiers";

import { buildClassOption } from "./chartOptions";
import { useEChart } from "./useEChart";
import { Gate, Widget } from "./WidgetParts";

import "./charts.css";

echarts.use([PieChart, TooltipComponent, CanvasRenderer]);

/** An aggregate has no class to filter by; it goes to the fleet's by-class table. */
export function classHref(e: Pick<ClassEps, "key">): string {
  return e.key === REST_KEY ? "/fleet" : `/fleet/sources?class=${encodeURIComponent(e.key)}`;
}

const share = (v: number | null) => (v === null ? NOT_MEASURED : `${(v * 100).toFixed(0)} %`);

function reasonOf(model: Model): string {
  const q = model.qualifiers.find((x) => x.kind === "INSUFFICIENT_DATA");
  return q && q.kind === "INSUFFICIENT_DATA" ? q.reason : "No class has a mean EPS.";
}

function Donut({ model }: { model: Model }) {
  const router = useRouter();
  const option = useMemo(
    () => (p: HomePalette) => buildClassOption(model.entries, p),
    [model.entries],
  );
  const ref = useEChart(option, (click) => {
    const entry = click.dataIndex != null ? model.entries[click.dataIndex] : undefined;
    if (entry) router.push(classHref(entry));
  });
  return (
    <div className="hc-donut" aria-hidden="true">
      <div ref={ref} className="hc-donut-canvas" />
      <div className="hc-donut-total">
        <span className="hc-donut-num">{formatEps(model.totalEps)}</span>
        <span className="hc-donut-unit">EPS</span>
      </div>
    </div>
  );
}

function List({ model }: { model: Model }) {
  return (
    <table className="hc-list">
      <caption className="sr-only">
        Mean EPS by source class, complete intervals only. Total {formatEps(model.totalEps)} EPS.
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
              <Link href={classHref(e)}>{e.label}</Link>
              {e.key === REST_KEY && model.restContains.length > 0 && (
                <span className="hc-rest" title={model.restText ?? undefined}>
                  {model.restContains.join(", ")}
                </span>
              )}
            </th>
            <td className="num">{formatEps(e.eps)}</td>
            <td className="num">{share(e.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EpsByClass() {
  const read = homeReads.fleetSummary("24h");
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? epsByClass(data) : null), [data]);

  return (
    <Widget
      id="home-eps-class"
      title="EPS by source class"
      windowText="24 h"
      badge={(model && badgeText(model.qualifiers)) || undefined}
      viewAll={{ href: "/fleet", label: "Fleet" }}
    >
      <Gate state={state} what="EPS by class" permission="read:fleet">
        {() =>
          model && (
            <>
              {model.entries.length === 0 ? (
                <p className="tw-state">{reasonOf(model)}</p>
              ) : (
                <div className="hc-class">
                  <Donut model={model} />
                  <List model={model} />
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
      </Gate>
    </Widget>
  );
}
