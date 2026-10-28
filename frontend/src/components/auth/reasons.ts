// Validation failures often carry a list. The page lists every item. A string
// is shown as sent. Nothing here rewrites a reason into a friendlier guess.

import { renderRefusal } from "@/i18n/refusalMessage";
import type { Locale } from "@/i18n/locale";
import type { TextTranslator } from "@/i18n/textTranslator";
import { ApiError } from "@/lib/api";
import { refusalSentences } from "@/lib/refusal";
import { englishText } from "./englishText";

function textOf(item: unknown): string {
  if (typeof item === "string") return item;
  if (item !== null && typeof item === "object" && "msg" in item) {
    const msg = (item as { msg?: unknown }).msg;
    if (typeof msg === "string") return msg;
  }
  return "";
}

/**
 * Every reason an `ApiError` carried, in order, with blanks removed. A reason that carries a
 * message code is worded in `locale` (English shows the server's text as sent); `t` must then
 * be a root translator (full keys) that holds the `backend` keys.
 */
export function errorTexts(
  err: unknown,
  t: TextTranslator = englishText,
  locale: Locale = "en",
): string[] {
  if (!(err instanceof ApiError)) {
    return [t("auth.requestSendFailed")];
  }
  const detail = err.detail;
  if (Array.isArray(detail)) {
    const sentences = refusalSentences(err).map((sentence) =>
      renderRefusal(sentence, locale, t),
    );
    if (sentences.length === detail.length && sentences.every((line) => line.trim() !== "")) {
      return sentences;
    }
    const lines = detail.map(textOf).filter((line) => line.trim() !== "");
    if (lines.length > 0) return lines;
  }
  if (typeof detail === "string" && detail.trim() !== "") {
    const [sentence] = refusalSentences(err);
    return [sentence ? renderRefusal(sentence, locale, t) : detail];
  }
  if (err.message.trim() !== "") return [err.message];
  return [t("auth.requestFailed")];
}
