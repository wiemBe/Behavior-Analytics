import { ApiError } from "@/lib/api";
import type { TextTranslator } from "@/i18n/textTranslator";

/**
 * The sentence a list page shows when its read fails, by what failed.
 *
 * `noun` is what the page lists ("alerts"); `grant` is the permission its GET
 * routes require, read from the route's guard in `backend/app/api/router.py`
 * (a page whose routes need two grants passes both, joined as text). A
 * non-`ApiError` is a network failure, so it takes the third sentence.
 */
export function loadErrorMessage(
  err: unknown,
  noun: string,
  grant: string,
  t?: TextTranslator,
): string {
  if (err instanceof ApiError && err.status === 403) {
    if (t) return t("errors.loadForbidden", { noun, grant });
    return `You do not have permission to view ${noun}. Ask an administrator for the ${grant} grant.`;
  }
  if (err instanceof ApiError && err.status === 401) {
    if (t) return t("errors.sessionExpired");
    return "Your session has expired. Sign in again to continue.";
  }
  const capitalised = noun.charAt(0).toUpperCase() + noun.slice(1);
  if (t) return t("errors.loadFailed", { noun: capitalised });
  return `${capitalised} could not be loaded. The backend may be unreachable; reload the page to try again.`;
}
