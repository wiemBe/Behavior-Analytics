// Validation failures often carry a list. The page lists every item. A string
// is shown as sent. Nothing here rewrites a reason into a friendlier guess.

import { ApiError } from "@/lib/api";

function textOf(item: unknown): string {
  if (typeof item === "string") return item;
  if (item !== null && typeof item === "object" && "msg" in item) {
    const msg = (item as { msg?: unknown }).msg;
    if (typeof msg === "string") return msg;
  }
  return "";
}

/** Every reason an `ApiError` carried, in order, with blanks removed. */
export function errorTexts(err: unknown): string[] {
  if (!(err instanceof ApiError)) {
    return ["The request could not be sent. Check your connection and try again."];
  }
  const detail = err.detail;
  if (Array.isArray(detail)) {
    const lines = detail.map(textOf).filter((line) => line.trim() !== "");
    if (lines.length > 0) return lines;
  }
  if (typeof detail === "string" && detail.trim() !== "") return [detail];
  if (err.message.trim() !== "") return [err.message];
  return ["The request failed."];
}
