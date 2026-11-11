// What a failed report read says. The wording never carries the exception's text. A refusal of the
// period (422, a coded sentence such as `reports.start_in_future`) is worded by the shared backend
// message renderer; a rate limit goes through the shared sentence.

import { ApiError, rateLimitedText } from "@/lib/api";
import type { BackendSentence } from "@/i18n/backendMessage";
import type { TextTranslator } from "@/i18n/textTranslator";
import { refusalSentences } from "@/lib/refusal";

export function reportLoadReason(
  err: unknown,
  say: TextTranslator,
  backendMessage: (sentence: BackendSentence) => string,
): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return rateLimitedText(err, say);
    if (err.status === 401) return say("reports.loadFailed.expired");
    if (err.status === 422) {
      const sentence = refusalSentences(err)[0];
      if (sentence) return say("reports.loadFailed.refused", { reason: backendMessage(sentence) });
    }
    return say("reports.loadFailed.server");
  }
  return say("reports.loadFailed.network");
}
