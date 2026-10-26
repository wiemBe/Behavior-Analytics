import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { customRuleLatencyFor } from "@/components/customrules/authoring";
import type { CustomRuleCatalog } from "@/lib/api";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

export function CustomRuleLatency({
  catalog,
  canLink,
  className,
}: {
  catalog: CustomRuleCatalog;
  canLink: boolean;
  className: string;
}) {
  const t = useTranslations("customRules.latency");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const parts = customRuleLatencyFor(catalog, locale, say);
  return (
    <p className={className}>
      {parts.lead}
      {canLink ? <Link href="/admin/search-cadence">{t("searchCadence")}</Link> : t("searchCadence")}
      {parts.tail}
    </p>
  );
}
