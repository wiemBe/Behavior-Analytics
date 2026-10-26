// The honesty line for every measured figure on the screen: how much of the
// requested span was actually collected. One banner, not a card; its state is
// named in words. For incomplete input the API's reason is the sentence: it
// already carries the coverage counts, so repeating the local breakdown would
// state the same fact twice.

import type { RuleStatisticsWindow } from "@/lib/api";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  daysLabel,
  formatCount,
  formatSpan,
  windowBreakdown,
  windowState,
} from "./ruleStatistics";

export function MeasurementWindow({ window: w }: { window: RuleStatisticsWindow }) {
  const t = useTranslations("rules.measurement");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const state = windowState(w);
  const requested = `${formatCount(w.days, locale)} ${daysLabel(w.days, say)}`;
  const fromHistory = w.history_windows ?? 0;

  return (
    <p
      className={`stats-window ${state === "complete" ? "ok" : state === "partial" ? "warn" : "neutral"}`}
      role="region"
      aria-label={t("label")}
    >
      {state === "complete" ? (
        <>
          <strong>{t("complete")}</strong>{" "}
          {t("completeSentence", {
            measured: formatSpan(w.measured_seconds, locale, say),
            requested,
            breakdown: windowBreakdown(w, locale, say),
          })}
        </>
      ) : (
        <>
          <strong>{state === "partial" ? t("partial") : t("insufficient")}</strong>{" "}
          {w.reason ??
            (state === "partial"
              ? t("partialFallback")
              : t("insufficientFallback"))}
        </>
      )}{" "}
      {fromHistory > 0 && (
        <>
          {t("history", {
            history: formatCount(fromHistory, locale),
            complete: formatCount(w.complete_windows, locale),
          })}{" "}
        </>
      )}
      {t("utc")}
    </p>
  );
}
