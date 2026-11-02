// The interface languages the code knows, the ones switched on, and the cookie that holds the
// choice.
//
// English is the default. The cookie is written by the client (account settings, session sync),
// so it is not HttpOnly; the server reads it on every request (`request.ts`).

export const LOCALES = ["en", "tr"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";

/**
 * THE SWITCH (D-41). The interface is English only from 0.1.10. Turkish is frozen, not deleted:
 * the code, `messages/tr/` and the Turkish contract tests stay, checkpointed at the tag
 * `checkpoint/bilingual-2026-11-02` (14064d19, every UI text in en + tr). A locale that is not
 * listed here resolves to English everywhere (`toLocale`): a `ba_locale=tr` cookie, an account
 * with `ui_language = "tr"`, the session sync, the server request config. The account page shows
 * the language control only while this holds more than one locale.
 *
 * To turn Turkish back on: set this to `["en", "tr"]`, translate the keys added to
 * `messages/en/` since the checkpoint (the contract test lists them as untranslated), and
 * re-tighten the Turkish contract tests (see docs/tasks/LANG-1.report.md).
 */
export const ENABLED_LOCALES: readonly Locale[] = ["en"];
export const LOCALE_COOKIE = "ba_locale";
const ONE_YEAR_SECONDS = 31_536_000;

export function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "tr";
}

/** A locale that is switched on (`ENABLED_LOCALES`). */
export function isEnabledLocale(value: unknown): value is Locale {
  return isLocale(value) && ENABLED_LOCALES.includes(value);
}

/** Any value that is not an enabled locale, or none, is English. */
export function toLocale(value: unknown): Locale {
  return isEnabledLocale(value) ? value : DEFAULT_LOCALE;
}

/** The locale named by a `document.cookie` string; English when the cookie is absent. */
export function localeFromCookieString(cookie: string): Locale {
  for (const part of cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === LOCALE_COOKIE) return toLocale(rest.join("="));
  }
  return DEFAULT_LOCALE;
}

/** The cookie the browser holds right now. Client only. */
export function readLocaleCookie(): Locale {
  if (typeof document === "undefined") return DEFAULT_LOCALE;
  return localeFromCookieString(document.cookie);
}

/**
 * Write `ba_locale` (path `/`, `SameSite=Lax`, one year; `Secure` on HTTPS) and read it back.
 * False when the browser did not keep it, so a caller never refreshes into the same language
 * forever. A locale that is not enabled is never written.
 */
export function writeLocaleCookie(locale: Locale): boolean {
  if (typeof document === "undefined" || !isEnabledLocale(locale)) return false;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  try {
    document.cookie =
      `${LOCALE_COOKIE}=${locale}; Path=/; Max-Age=${ONE_YEAR_SECONDS}; SameSite=Lax${secure}`;
  } catch {
    return false;
  }
  return readLocaleCookie() === locale;
}
