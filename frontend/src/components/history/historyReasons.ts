// The backend's sentences of the history backfill page, in the reader's language.
//
// Kept apart from `historyText.ts` on purpose: reading a sentence by its code pulls the generated
// code table and the `backend` catalog into a route, and the first-run dialog is mounted in the
// shell, which carries neither.

import { renderBackendMessage } from "@/i18n/backendMessage";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import { ENGLISH_HISTORY, type SentenceReader } from "./historyText";

/**
 * A reader that words a sentence by its code. English shows the API's text exactly as sent;
 * another language shows its catalog entry for the code, and the API's text when the sentence has
 * no code or the code has no entry (a row stored before codes existed).
 */
export function sentenceReader(i18n: BehaviorI18n = ENGLISH_HISTORY): SentenceReader {
  return (text, coded) => {
    const rendered = renderBackendMessage(
      { text, code: coded?.code, params: coded?.params },
      i18n.locale,
      i18n.t,
    );
    // The catalog words a reason so that it also fits inside a longer one ("...; the width is 50
    // buckets"), so it starts lowercase there. A reason shown on its own opens a sentence.
    if (i18n.locale === "en" || rendered === text || rendered === "") return rendered;
    return rendered.charAt(0).toLocaleUpperCase("tr-TR") + rendered.slice(1);
  };
}
