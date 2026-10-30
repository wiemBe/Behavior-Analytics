// What a failed read of the IPS administration API says. Never the raw error:
// it can carry an upstream URL, and this text goes into the DOM.

import { ApiError, rateLimitedText } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import { ENGLISH_IPS } from "@/components/ips/ipsI18n";

export function readError(err: unknown, what: string, i18n: BehaviorI18n = ENGLISH_IPS): string {
  if (err instanceof ApiError && err.status === 429) return rateLimitedText(err, i18n.t);
  if (err instanceof ApiError && err.status === 403) {
    return i18n.t("ips.read.forbidden", { noun: what });
  }
  if (err instanceof ApiError && err.status === 401) {
    return i18n.t("ips.read.expired");
  }
  // English opens the sentence with the noun, so it is capitalised there; Turkish
  // opens with "Okunamadı:" and keeps the noun as it is.
  const noun =
    i18n.locale === "en" ? what.charAt(0).toUpperCase() + what.slice(1) : what;
  return i18n.t("ips.read.loadFailed", { noun });
}
