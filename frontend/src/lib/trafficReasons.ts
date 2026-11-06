// A reason code from the traffic routes, as a sentence. The backend sends only the code
// (`waf_traffic_detail.reason.*`, `ips_traffic_detail.reason.*`); its sentence is in the generated
// backend catalog. A code this page does not know gets the generic sentence, never the code.

import { useLocale, useTranslations } from "next-intl";

import { renderBackendCode } from "@/i18n/backendMessage";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

const REASONS = [
  "not_collected",
  "no_complete_bucket",
  "no_usable_bucket",
  "truncated",
  "buckets_missing",
  "bucket_partial",
] as const;

export const TRAFFIC_REASON_CODES: readonly string[] = (["waf", "ips"] as const).flatMap((family) =>
  REASONS.map((reason) => `${family}_traffic_detail.reason.${reason}`),
);

/** The sentence for a reason code, or null when the response carries none. */
export function useTrafficReason(code: string | null | undefined): string | null {
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const own = useTranslations("traffic.reasons");
  if (!code) return null;
  const known = TRAFFIC_REASON_CODES.includes(code)
    ? renderBackendCode({ code, params: {} }, locale, say)
    : undefined;
  return known ?? own("unknown");
}
