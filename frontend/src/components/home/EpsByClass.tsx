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
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { fleetDrillHref, CLASS_LABELS, type FleetWindow } from "@/lib/fleet";
import type { HomePalette } from "@/lib/home/chartPalette";
import { sliceVar } from "@/lib/home/chartPalette";
import { epsByClass, REST_KEY, type ClassEps, type EpsByClass as Model } from "@/lib/home/epsByClass";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { buildClassOption } from "./chartOptions";
import { badgePhrase, reasonText } from "./qualifierText";
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

/** The class a label names, found by the label the derivation took from `CLASS_LABELS`. */
const CLASS_OF_LABEL = new Map(Object.entries(CLASS_LABELS).map(([key, label]) => [label, key]));

/** The words of the entries and the figures, in the interface language. */
function useClassWords() {
  const t = useTranslations("home.epsClass");
  const measures = useMeasures();
  const { label } = useEnumLabels();
  return {
    t,
    measures,
    /** An entry's name: a class by its key, the aggregate by its own word. */
    entry: (e: Pick<ClassEps, "key" | "label">) =>
      e.key === REST_KEY ? t("rest") : label("sourceClass", e.key),
    /** A class named by its English label (the aggregate's contents). */
    byLabel: (english: string) => {
      const key = CLASS_OF_LABEL.get(english);
      return key ? label("sourceClass", key) : english;
    },
    share: (v: number | null) => measures.percent(v, 0),
    eps: (v: number | null | undefined) => measures.eps(v),
  };
}

function Donut({ model, window }: { model: Model; window: FleetWindow }) {
  const router = useRouter();
  const units = useTranslations("units");
  const say = asTextTranslator(useTranslations());
  const words = useClassWords();
  const entries = useMemo(
    () => model.entries.map((e) => ({ ...e, label: words.entry(e) })),
    // `words.entry` follows the language; the model is what changes the entries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model.entries, words.measures.locale],
  );
  const option = useMemo(
    () => (p: HomePalette) =>
      buildClassOption(entries, p, undefined, { t: say, locale: words.measures.locale }),
    [entries, say, words.measures.locale],
  );
  const ref = useEChart(option, (click) => {
    const entry = click.dataIndex != null ? model.entries[click.dataIndex] : undefined;
    if (entry) router.push(classHref(entry, window));
  });
  return (
    <div className="hc-donut" aria-hidden="true">
      <div ref={ref} className="hc-donut-canvas" />
      <div className="hc-donut-total">
        <span className="hc-donut-num">{words.eps(model.totalEps)}</span>
        <span className="hc-donut-unit">{units("eps")}</span>
      </div>
    </div>
  );
}

function List({ model, window }: { model: Model; window: FleetWindow }) {
  const words = useClassWords();
  const { t } = words;
  const units = useTranslations("units");
  const restText = model.restContains.length
    ? t("restContents", { classes: model.restContains.map(words.byLabel).join(", ") })
    : undefined;
  return (
    <table className="hc-list">
      <caption className="sr-only">{t("caption", { eps: words.eps(model.totalEps) })}</caption>
      <thead>
        <tr>
          <th scope="col">{t("class")}</th>
          <th scope="col" className="num">
            {units("eps")}
          </th>
          <th scope="col" className="num">
            {t("share")}
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
              <Link href={classHref(e, window)}>{words.entry(e)}</Link>
              {e.key === REST_KEY && model.restContains.length > 0 && (
                <span className="hc-rest" title={restText}>
                  {model.restContains.map(words.byLabel).join(", ")}
                </span>
              )}
            </th>
            <td className="num">{words.eps(e.eps)}</td>
            <td className="num">{words.share(e.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EpsByClass({ range }: RangedProps = {}) {
  const t = useTranslations("home.epsClass");
  const history = useTranslations("home.history");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const copy = useWidgetCopy();
  const window = useEffectiveRange(range);
  const read = homeReads.fleetSummary(window);
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? epsByClass(data) : null), [data]);
  const reasonOf = (m: Model): string => {
    const q = m.qualifiers.find((x) => x.kind === "INSUFFICIENT_DATA");
    return q && q.kind === "INSUFFICIENT_DATA" ? reasonText(say, q) : t("noMean");
  };
  const complete = data?.classes.reduce((sum, c) => sum + c.buckets.complete, 0) ?? 0;
  const fromHistory =
    data?.classes.reduce((sum, c) => sum + c.buckets.complete_from_history, 0) ?? 0;

  return (
    <WidgetFrame
      id="home-eps-class"
      title={t("title")}
      windowText={copy.range(window)}
      badge={(model && badgePhrase(say, measures, model.qualifiers)) || undefined}
      link={{ href: fleetDrillHref(window), label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:fleet"
    >
      {() =>
          model && (
            <>
              {fromHistory > 0 && (
                <p className="tw-note">
                  {history("buckets", {
                    from: measures.count(fromHistory),
                    complete: measures.count(complete),
                  })}
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
                <p className="tw-note">{t("withoutData", { count: model.classesWithoutData })}</p>
              )}
            </>
          )
      }
    </WidgetFrame>
  );
}
