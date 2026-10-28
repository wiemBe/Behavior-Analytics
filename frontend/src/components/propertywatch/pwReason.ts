// The backend's sentences of the property-watch screens, in the reader's language.
//
// Kept apart from `pwFormat.ts` on purpose: reading a sentence by its code pulls the `backend`
// catalog (and the namespaces its parameters use) into a route, and the screens that print no
// backend sentence (the anomaly list, the series chart) must not carry them.

import type { PwScopeOption, PwWatchStatus } from "@/lib/api";
import { renderBackendMessage } from "@/i18n/backendMessage";

import { statusReason } from "./pwFormat";
import { ENGLISH_PW, type PwI18n } from "./pwText";

/** The registry code of a backend sentence and its raw values, as the API sends them beside it. */
export interface PwCoded {
  code?: string | null;
  params?: Record<string, unknown> | null;
}

/**
 * A sentence the backend wrote, in the reader's language: English shows the API's text exactly as
 * sent; another language shows its catalog entry for the sentence's code, and the API's text when
 * the sentence has no code or the code has no entry (a row stored before codes existed).
 */
export function pwSentence(
  text: string,
  coded: PwCoded | null | undefined,
  i18n: PwI18n = ENGLISH_PW,
): string {
  return renderBackendMessage(
    { text, code: coded?.code, params: coded?.params },
    i18n.locale,
    i18n.t,
  );
}

/** A watch's status sentence: the API's reason in the reader's language, else the status' meaning. */
export function codedStatusReason(
  status: PwWatchStatus | null,
  reason: string | null,
  i18n: PwI18n,
  coded: PwCoded | null | undefined,
): string {
  return reason ? pwSentence(reason, coded, i18n) : statusReason(status, null, i18n);
}

/** Why a picker option's scope did not resolve, in the reader's language; null when it did. */
export function scopeReason(
  option: Pick<PwScopeOption, "scope_reason" | "scope_reason_code" | "scope_reason_params">,
  i18n: PwI18n = ENGLISH_PW,
): string | null {
  return option.scope_reason
    ? pwSentence(
        option.scope_reason,
        { code: option.scope_reason_code, params: option.scope_reason_params },
        i18n,
      )
    : null;
}
