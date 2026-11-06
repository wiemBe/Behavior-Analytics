// The alert title an analyst reads (SOC-1b, QW3).
//
// The backend builds the title from a message code and writes the detector's enum value into it
// ("VOLUME_DROP on Edge firewall"). The code and its params travel beside the text, so the page
// swaps the leading enum value for the detector's name from the `enums` catalog and keeps the rest
// of the backend sentence untouched. A title the page cannot map (no code, another code, a
// detector the catalog lacks, a sentence that does not start with the value) stays as sent.

import { enumLabel, type EnumGroup } from "@/i18n/enumLabels";
import { renderBackendMessage } from "@/i18n/backendMessage";
import type { Locale } from "@/i18n/locale";
import type { TextTranslator } from "@/i18n/textTranslator";
import type { Alert } from "@/lib/api";

/** The alert title codes that open with a detector value, and where that value sits. */
const DETECTOR_TITLES: Record<string, { param: string; group: EnumGroup | "propertyWatchAnomalyType" }> = {
  "alert.anomaly.title": { param: "anomaly_type", group: "anomalyType" },
  "alert.waf.title": { param: "detector", group: "anomalyType" },
  "alert.ips.title.source": { param: "detector", group: "anomalyType" },
  "alert.ips.title.destination_port": { param: "detector", group: "anomalyType" },
  "alert.ips.title.destination_ip": { param: "detector", group: "anomalyType" },
  "alert.ips.title.signature": { param: "detector", group: "anomalyType" },
  "alert.pw.title": { param: "detector", group: "propertyWatchAnomalyType" },
};

/** The English title with the detector named; `null` when the title is not one of these. */
export function humanAlertTitle(
  alert: Pick<Alert, "title" | "title_code" | "title_params">,
  say: TextTranslator,
): string | null {
  const spec = alert.title_code ? DETECTOR_TITLES[alert.title_code] : undefined;
  const raw = spec ? alert.title_params?.[spec.param] : undefined;
  if (!spec || typeof raw !== "string" || !alert.title.startsWith(`${raw} `)) return null;
  const name = enumLabel(say, spec.group, raw);
  return name === raw ? null : `${name}${alert.title.slice(raw.length)}`;
}

export function alertTitleText(
  alert: Pick<Alert, "title" | "title_code" | "title_params">,
  locale: Locale,
  say: TextTranslator,
): string {
  if (locale === "en") {
    const human = humanAlertTitle(alert, say);
    if (human) return human;
  }
  return renderBackendMessage(
    { text: alert.title, code: alert.title_code, params: alert.title_params },
    locale,
    say,
  );
}
