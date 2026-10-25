"use client";

// chart.search_results (Phase F §F.9.2). One search's stored aggregates, drawn by the same
// ResultTrend the search page uses. The layout holds a search id and a metric key, both
// enumerated. The dashboard range filters the trend read, which is the only read that takes one.

import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { ResultTrend } from "@/components/ResultTrend";
import { ConfigNotice } from "@/components/catalog/ConfigNotice";
import type { TrendI18n } from "@/components/trendText";
import { useApiResource } from "@/hooks/useApiResource";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, actionErrorMessage, api } from "@/lib/api";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, type RangedProps } from "@/widgets/runtime";

export interface SearchResultsConfig {
  search_id: string | null;
  metric_key: string | null;
}

const SERIES = new Set(["LINE", "BAR", "SINGLE_VALUE"]);

export function SearchResultsWidget({
  id,
  config,
  range,
}: { id: string; config: SearchResultsConfig } & RangedProps) {
  const t = useTranslations("widgets.searchResults");
  const copy = useWidgetCopy();
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as TrendI18n["locale"];
  const trendText = useMemo<TrendI18n>(() => ({ t: say, locale }), [say, locale]);
  const window = useEffectiveRange(range);
  const searchId = typeof config.search_id === "string" && config.search_id !== "" ? config.search_id : null;
  const searchState = useApiResource(
    searchId ? `/searches/${searchId}` : null,
    (signal) => api.search(searchId as string, signal),
  );

  const loaded = searchState.status === "ok" ? searchState.data : null;
  const viz = (loaded?.visualization_type ?? "LINE").toUpperCase();
  const title = loaded?.name ?? t("defaultTitle");
  let body;
  if (searchId === null) {
    body = (
      <ConfigNotice
        lead={t("chooseLead")}
        text={t("chooseText")}
        hint={t("chooseHint")}
      />
    );
  } else if (searchState.status === "loading" || searchState.status === "idle") {
    body = (
      <p className="tw-state" role="status">
        {t("loading")}
      </p>
    );
  } else if (searchState.status === "forbidden") {
    body = (
      <p className="tw-state" role="alert">
        {t("forbidden")}
      </p>
    );
  } else if (searchState.status === "error" && searchState.httpStatus === 404) {
    body = (
      <ConfigNotice
        lead={t("deletedLead")}
        text={t("deletedText")}
        hint={t("deletedHint")}
      />
    );
  } else if (searchState.status === "error" || loaded === null) {
    const status = searchState.status === "error" ? searchState.httpStatus : null;
    body = (
      <p className="tw-state" role="alert">
        {actionErrorMessage(new ApiError(status ?? 500, ""), say)}
      </p>
    );
  } else {
    body = (
      <ResultTrend
        variant="widget"
        honourRange
        range={range}
        searchId={searchId}
        search={loaded}
        visualization={loaded.visualization_type}
        metricKey={config.metric_key ?? undefined}
        i18n={trendText}
      />
    );
  }

  return (
    <WidgetFrame
      id={`search-results-${id}`}
      title={title}
      what={t("what")}
      permission="read:searches"
      windowText={searchId !== null && SERIES.has(viz) ? copy.range(window) : undefined}
      link={{ href: searchId ? `/searches/${searchId}` : "/searches", label: t("link") }}
      fillBody
      state={{ kind: "ok", data: null, asOf: null }}
    >
      {() => body}
    </WidgetFrame>
  );
}
