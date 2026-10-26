"use client";

import { LineChart } from "echarts/charts";
import {
  GraphicComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useRef } from "react";

import { TURKISH_ECHARTS_LOCALE } from "@/components/home/chartLocale";
import { formatValue } from "@/components/customrules/authoring";
import { customRulesEnglishText } from "@/components/customrules/englishText";
import type { CustomRulePreview } from "@/lib/api";
import { readChartPalette, type ChartPalette } from "@/lib/chartPalette";
import type { Locale } from "@/i18n/locale";
import { toLocale } from "@/i18n/locale";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";

import type { EChartsOption } from "echarts";
import { utcTimeHtml } from "@/components/ui/UtcTime";


echarts.use([
  LineChart,
  GraphicComponent,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

type AreaPair = [
  { xAxis: number; itemStyle: { color: string }; name: string },
  { xAxis: number },
];

export function previewTooltip(
  preview: CustomRulePreview,
  subjectLabel: string,
  dataIndex: number,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): string {
  const bucket = preview.buckets[dataIndex];
  if (!bucket) return "";
  const safeSubject = echarts.format.encodeHTML(subjectLabel);
  const value =
    bucket.measurement === "NOT_MEASURED" || bucket.value === null
      ? t("customRules.preview.notMeasured")
      : bucket.measurement === "LOWER_BOUND"
        ? t("customRules.preview.atLeast", { value: formatValue(bucket.value, preview.unit, locale) })
        : formatValue(bucket.value, preview.unit, locale);
  const measurement = t(`customRules.preview.measurementKinds.${bucket.measurement}`);
  const completenessKey = bucket.completeness
    ? `customRules.preview.completenessKinds.${bucket.completeness}`
    : null;
  const completeness = completenessKey && t.has?.(completenessKey)
    ? t(completenessKey)
    : bucket.completeness;
  return [
    safeSubject,
    utcTimeHtml(bucket.bucket_start),
    t("customRules.preview.reading", { value }),
    t("customRules.preview.measurement", { value: measurement }),
    completeness ? t("customRules.preview.completeness", { value: completeness }) : "",
    t(
      bucket.in_maintenance
        ? "customRules.preview.maintenanceYes"
        : "customRules.preview.maintenanceNo",
    ),
  ]
    .filter(Boolean)
    .join("<br/>");
}

export function previewText(
  preview: CustomRulePreview,
  subjectLabel: string,
  _locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): string {
  const lowerBounds = preview.buckets.filter((bucket) => bucket.measurement === "LOWER_BOUND");
  const maintenance = preview.buckets.filter((bucket) => bucket.in_maintenance).length;
  const lowerText =
    lowerBounds.length === 0
      ? t("customRules.preview.noLower")
      : t("customRules.preview.lower", { count: lowerBounds.length });
  return t("customRules.preview.text", {
    subject: subjectLabel || t("customRules.preview.selectedSubject"),
    fired: preview.fired_count,
    measured: preview.measured_count,
    unmeasured: preview.unmeasured_count,
    lower: lowerText,
    maintenance,
  });
}

export function buildPreviewOption(
  preview: CustomRulePreview,
  subjectLabel: string,
  palette: ChartPalette,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): EChartsOption {
  const times = preview.buckets.map((bucket) => Date.parse(bucket.bucket_start));
  const lastTime = times[times.length - 1] ?? Date.now();
  const areas: AreaPair[] = [
    ...preview.breach_intervals.map(
      (interval): AreaPair => [
        {
          xAxis: Date.parse(interval.start),
          itemStyle: { color: palette.anomaly },
          name: t("customRules.preview.breachInterval"),
        },
        { xAxis: interval.end ? Date.parse(interval.end) : lastTime },
      ],
    ),
    ...preview.buckets
      .filter((bucket) => bucket.in_maintenance)
      .map(
        (bucket): AreaPair => [
          {
            xAxis: Date.parse(bucket.bucket_start),
            itemStyle: { color: palette.gap },
            name: t("customRules.preview.maintenanceBucket"),
          },
          { xAxis: Date.parse(bucket.bucket_start) + 300_000 },
        ],
      ),
  ];
  const text = { color: palette.label, fontFamily: palette.font };
  const thresholdText = t("customRules.preview.threshold", {
    value: formatValue(preview.threshold_value, preview.unit, locale),
  });
  return {
    animation: false,
    useUTC: true,
    graphic: {
      type: "text",
      top: 4,
      right: 8,
      style: {
        text: thresholdText,
        fill: palette.expected,
        fontFamily: palette.font,
      },
    },
    grid: { left: 68, right: 24, top: 32, bottom: 52 },
    textStyle: text,
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontFamily: palette.font },
      extraCssText: "box-shadow: none;",
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        return previewTooltip(preview, subjectLabel, first?.dataIndex ?? -1, locale, t);
      },
    },
    xAxis: {
      type: "time",
      name: t("customRules.preview.timeAxis"),
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: text,
      axisLabel: text,
      axisLine: { lineStyle: { color: palette.axis } },
    },
    yAxis: {
      type: "value",
      name: preview.unit,
      nameTextStyle: text,
      min: 0,
      axisLabel: text,
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        type: "line",
        name: t("customRules.preview.readingSeries", { unit: preview.unit }),
        data: preview.buckets.map((bucket) => [
          Date.parse(bucket.bucket_start),
          bucket.measurement === "NOT_MEASURED" ? null : bucket.value,
        ]),
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: palette.observed, width: 1.5 },
        itemStyle: { color: palette.observed },
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: palette.expected, type: "dashed" },
          data: [{ yAxis: preview.threshold_value }],
        },
        markArea: {
          silent: true,
          label: { show: false },
          data: areas,
        },
        markPoint: {
          silent: true,
          symbol: "diamond",
          symbolSize: 9,
          label: { show: false },
          itemStyle: { color: palette.warn },
          data: preview.buckets.flatMap((bucket) =>
            bucket.measurement === "LOWER_BOUND" && bucket.value !== null
              ? [{
                  coord: [Date.parse(bucket.bucket_start), bucket.value],
                  name: t("customRules.preview.atLeast", {
                    value: formatValue(bucket.value, preview.unit, locale),
                  }),
                }]
              : [],
          ),
        },
      },
    ],
  };
}

export function PreviewChart({ preview, subjectLabel }: { preview: CustomRulePreview; subjectLabel: string }) {
  const locale = toLocale(useLocale());
  const t = useTranslations("customRules.preview");
  const say = asTextTranslator(useTranslations());
  const ref = useRef<HTMLDivElement | null>(null);
  const text = useMemo(
    () => previewText(preview, subjectLabel, locale, say),
    [locale, preview, say, subjectLabel],
  );

  useEffect(() => {
    const element = ref.current;
    if (!element || preview.buckets.length === 0) return;
    const chart = echarts.init(
      element,
      null,
      locale === "tr" ? { locale: TURKISH_ECHARTS_LOCALE as never } : undefined,
    );
    chart.setOption(buildPreviewOption(preview, subjectLabel, readChartPalette(), locale, say));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [locale, preview, say, subjectLabel]);

  if (preview.buckets.length === 0) {
    return (
      <div className="notice">
        <p><strong>{t("emptyTitle")}</strong></p>
        <p>{t("emptyHelp")}</p>
      </div>
    );
  }

  return (
    <>
      <div ref={ref} className="cr-chart" role="img" aria-label={text} />
      <p className="sr-only">{text}</p>
    </>
  );
}

export function PreviewSummary({ preview }: { preview: CustomRulePreview }) {
  const t = useTranslations("customRules.preview");
  const total = preview.measured_count + preview.unmeasured_count;
  const majority = total > 0 && preview.unmeasured_count > total / 2;
  const lowerBounds = preview.buckets.filter((bucket) => bucket.measurement === "LOWER_BOUND");
  const maintenance = preview.buckets.filter((bucket) => bucket.in_maintenance).length;
  return (
    <div className="cr-preview-summary" aria-label={t("summaryLabel")}>
      <p><strong>{t("fired", { count: preview.fired_count })}</strong></p>
      <dl>
        <div><dt>{t("measured")}</dt><dd className="num">{t("bucketCount", { count: preview.measured_count })}</dd></div>
        <div><dt>{t("unmeasured")}</dt><dd className="num">{t("bucketCount", { count: preview.unmeasured_count })}</dd></div>
        <div><dt>{t("lowerBound")}</dt><dd className="num">{t("bucketCount", { count: lowerBounds.length })}</dd></div>
        <div><dt>{t("maintenance")}</dt><dd className="num">{t("bucketCount", { count: maintenance })}</dd></div>
      </dl>
      {majority && <p className="cr-honesty">{t("majority")}</p>}
      {preview.measured_count === 0 && <p className="cr-honesty">{t("noneMeasured")}</p>}
      {lowerBounds.length > 0 && <p className="cr-honesty">{t("lowerHelp")}</p>}
      {maintenance > 0 && <p className="cr-honesty">{t("maintenanceHelp")}</p>}
    </div>
  );
}
