// The two interface languages and the cookie that holds the choice.
//
// English is the default. The cookie is written by the client (account settings, session sync),
// so it is not HttpOnly; the server reads it on every request (`request.ts`).

export const LOCALES = ["en", "tr"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_COOKIE = "ba_locale";
const ONE_YEAR_SECONDS = 31_536_000;

export function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "tr";
}

/** Any value other than `en` / `tr`, or none, is English. */
export function toLocale(value: unknown): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
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
 * forever.
 */
export function writeLocaleCookie(locale: Locale): boolean {
  if (typeof document === "undefined") return false;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  try {
    document.cookie =
      `${LOCALE_COOKIE}=${locale}; Path=/; Max-Age=${ONE_YEAR_SECONDS}; SameSite=Lax${secure}`;
  } catch {
    return false;
  }
  return readLocaleCookie() === locale;
}
