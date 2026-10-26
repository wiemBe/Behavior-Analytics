import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { DAY_CHOICES } from "@/components/rules/ruleStatistics";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { formatCount } from "@/lib/format";

export default function Loading() {
  const t = useTranslations("rules.statistics");
  const boundary = useTranslations("rules.boundary");
  const locale = useLocale() as Locale;
  const daySelector = (
    <nav className="range-selector stats-days" aria-label={t("daysLabel")}>
      <span className="muted">{t("window")}</span>
      {DAY_CHOICES.map((choice) => (
        <Link
          key={choice}
          href={`/rules/statistics?days=${choice}`}
          className={`range-option${choice === 7 ? " range-option-active" : ""}`}
          aria-current={choice === 7 ? "page" : undefined}
        >
          {formatCount(choice, locale)} {choice === 1 ? t("day") : t("days")}
        </Link>
      ))}
    </nav>
  );

  return (
    <BoundaryLoading
      title={t("title")}
      what={boundary("statisticsWhat")}
      description={t("description")}
      actions={daySelector}
    />
  );
}
