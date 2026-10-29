// The sentences of the first-run dialog, in the reader's language.
//
// The dialog is mounted in the shell, which carries the `backend` catalog only as single keys
// (`SHELL_BACKEND_KEYS`) and none of the generated code table, so it reads a code through the lean
// refusal reader. A code that reader cannot word, or that the shell does not carry, shows the API's
// text as sent. The history page itself uses `sentenceReader` (./historyReasons).

import { renderRefusal } from "@/i18n/refusalMessage";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import { ENGLISH_HISTORY, type SentenceReader } from "./historyText";

export function shellSentenceReader(i18n: BehaviorI18n = ENGLISH_HISTORY): SentenceReader {
  return (text, coded) => {
    const rendered = renderRefusal(
      { text, code: coded?.code, params: coded?.params },
      i18n.locale,
      i18n.t,
    );
    // The catalog words a reason so that it also fits inside a longer one, so it starts
    // lowercase; a reason shown on its own opens a sentence.
    if (i18n.locale === "en" || rendered === text || rendered === "") return rendered;
    return rendered.charAt(0).toLocaleUpperCase("tr-TR") + rendered.slice(1);
  };
}
