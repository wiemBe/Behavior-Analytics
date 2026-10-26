import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";

import { asTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import type { FormatLocale } from "@/lib/format";

/** The translator and locale of the interface, for a component that calls the area's helpers. */
export function useBehaviorI18n(): BehaviorI18n {
  const t = useTranslations();
  const locale = useLocale() as FormatLocale;
  return useMemo(() => ({ t: asTextTranslator(t), locale }), [t, locale]);
}
