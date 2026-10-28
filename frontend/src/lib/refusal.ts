// What a refused request carries beside its English sentence (MSG-1).
//
// `detail` is the sentence exactly as the server sent it. A coded refusal adds `detail_code` and
// `detail_params` beside it, a list of sentences a parallel `detail_codes`, and an object detail
// (`{message, ...}`) carries `code` and `params` inside itself. These readers only pull the
// sentences out; rendering one in the reader's language is `i18n/backendMessage.ts`. A refusal
// from an older server, or one that names no code, yields sentences with no code, and those render
// as the server's own words.

import type { ApiError } from "@/lib/api";

/** A sentence and the code the server gave it, if any: `i18n/backendMessage.ts` renders it. */
export interface RefusalSentence {
  text: string;
  code?: string | null;
  params?: Record<string, unknown> | null;
}

export const PERMISSION_CODE = "auth.permission.missing";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const codeOf = (value: unknown): string | null =>
  typeof value === "string" && value !== "" ? value : null;

const paramsOf = (value: unknown): Record<string, unknown> | null =>
  isRecord(value) ? value : null;

/**
 * The sentences a failed request carried, one per sentence the server wrote: the string detail,
 * each item of a list detail, or the `message` of an object detail. Empty when the detail holds
 * none (a validation array of objects, an empty body).
 */
export function refusalSentences(error: Pick<ApiError, "detail" | "body">): RefusalSentence[] {
  const detail = error.detail;
  const body = isRecord(error.body) ? error.body : {};
  if (typeof detail === "string") {
    return detail === ""
      ? []
      : [{ text: detail, code: codeOf(body.detail_code), params: paramsOf(body.detail_params) }];
  }
  if (Array.isArray(detail)) {
    const codes = Array.isArray(body.detail_codes) ? body.detail_codes : [];
    const out: RefusalSentence[] = [];
    detail.forEach((item, index) => {
      if (typeof item !== "string" || item.trim() === "") return;
      const coded = codes[index];
      out.push({
        text: item,
        code: isRecord(coded) ? codeOf(coded.code) : null,
        params: isRecord(coded) ? paramsOf(coded.params) : null,
      });
    });
    return out;
  }
  if (isRecord(detail) && typeof detail.message === "string" && detail.message !== "") {
    return [{ text: detail.message, code: codeOf(detail.code), params: paramsOf(detail.params) }];
  }
  return [];
}

/** The permission a 403 says is missing, when the server named it by code. */
export function missingPermission(error: Pick<ApiError, "status" | "detail" | "body">): string | null {
  if (error.status !== 403) return null;
  const [sentence] = refusalSentences(error);
  if (sentence?.code !== PERMISSION_CODE) return null;
  const permission = sentence.params?.permission;
  return typeof permission === "string" && permission !== "" ? permission : null;
}
