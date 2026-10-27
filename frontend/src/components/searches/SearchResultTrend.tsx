"use client";

// The result trend of the search page. `ResultTrend` takes its words as a prop (a function cannot
// cross from the server page), so this client wrapper reads the interface language and passes it.

import { useLocale, useTranslations } from "next-intl";
import { useMemo, type ComponentProps } from "react";

import { ResultTrend } from "@/components/ResultTrend";
import type { TrendI18n } from "@/components/trendText";
import { asTextTranslator } from "@/i18n/textTranslator";

export function SearchResultTrend(props: Omit<ComponentProps<typeof ResultTrend>, "i18n">) {
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as TrendI18n["locale"];
  const i18n = useMemo<TrendI18n>(() => ({ t: say, locale }), [say, locale]);
  return <ResultTrend {...props} i18n={i18n} />;
}
