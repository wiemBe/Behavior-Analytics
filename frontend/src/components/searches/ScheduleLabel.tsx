// A schedule as an analyst reads it: a preset name, or "Custom" with the cron
// kept visible. Shared by the catalog and the search page so the same cron is
// never worded two ways.

import { useLocale, useTranslations } from "next-intl";

import "@/components/ui/controls.css";

import { asTextTranslator } from "@/i18n/textTranslator";
import type { FormatLocale } from "@/lib/format";
import { cronToPreset, everyLabel } from "@/lib/searchCadence";

export function ScheduleLabel({ cron }: { cron: string }) {
  const t = useTranslations("cadence.schedule");
  // No hook beyond next-intl's: this label is also rendered by a server page.
  const i18n = { t: asTextTranslator(useTranslations()), locale: useLocale() as FormatLocale };
  const preset = cronToPreset(cron);
  if (preset) return <>{everyLabel(preset.seconds, i18n)}</>;
  return (
    <>
      {t("custom")} <code className="cron">{cron}</code>
    </>
  );
}
