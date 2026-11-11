// What a failed handover read says. The wording never carries the exception's text; a rate limit
// goes through the shared sentence, and a 404 names the missing record.

import { ApiError, rateLimitedText } from "@/lib/api";
import type { TextTranslator } from "@/i18n/textTranslator";

export function handoverLoadReason(err: unknown, say: TextTranslator): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return rateLimitedText(err, say);
    if (err.status === 403) return say("handover.loadFailed.forbidden");
    if (err.status === 401) return say("handover.loadFailed.expired");
    if (err.status === 404) return say("handover.loadFailed.notFound");
    return say("handover.loadFailed.server");
  }
  return say("handover.loadFailed.network");
}
