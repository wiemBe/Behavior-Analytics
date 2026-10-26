import { getLocale, getTranslations } from "next-intl/server";

import { enumLabel, statePillLabel, type EnumGroup, type EnumTranslator } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import type { FormatLocale } from "@/lib/format";

/** The translator and locale of the interface, for a server page. */
export async function getBehaviorI18n(): Promise<BehaviorI18n> {
  const t = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  return { t, locale };
}

/** The enum labels (`useEnumLabels`' counterpart) for a server page. */
export async function getEnumLabels() {
  const t = (await getTranslations("enums")) as unknown as EnumTranslator;
  return {
    label: (group: EnumGroup, value: string) => enumLabel(t, group, value),
    severityPill: (value: string) => enumLabel(t, "severityPill", value),
    statePill: (value: string) => statePillLabel(t, value),
  };
}
