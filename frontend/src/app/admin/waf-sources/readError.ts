// What a failed read of the WAF administration API says. Never the raw error:
// it can carry an upstream URL, and this text goes into the DOM.

import { ApiError, rateLimitedText } from "@/lib/api";
import { ENGLISH_WAF, type BehaviorI18n } from "@/components/waf/wafI18n";

export function readError(
  err: unknown,
  what: string,
  i18n: BehaviorI18n = ENGLISH_WAF,
): string {
  if (err instanceof ApiError && err.status === 429) return rateLimitedText(err, i18n.t);
  if (err instanceof ApiError && err.status === 403) {
    return i18n.t("waf.admin.read.forbidden", { what });
  }
  if (err instanceof ApiError && err.status === 401) {
    return i18n.t("waf.admin.read.expired");
  }
  const subject = i18n.locale === "en" ? `${what[0].toUpperCase()}${what.slice(1)}` : what;
  return i18n.t("waf.admin.read.failed", { what: subject });
}
