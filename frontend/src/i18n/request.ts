// Interface language (D-37): English and Turkish, no locale in the URL.
//
// Which hook a component uses, so a route task does not have to rediscover it:
//   - a server component (async page or layout): `getTranslations(namespace)` / `getLocale()`
//     from "next-intl/server";
//   - a client component ("use client") and a shared component that is neither (UI parts that
//     both kinds import, such as Pagination): `useTranslations(namespace)` / `useLocale()` from
//     "next-intl";
//   - a pure function that returns text takes the translator as its last, optional argument
//     (`t`, defaulting to English from `./fallback`) so an unconverted caller keeps today's text.
// Words the backend writes are not translated here (phase 1). Numbers go through
// `formatCount` / `formatPercent` / `formatShare` with the locale passed explicitly.
//
// The locale is the `ba_locale` cookie; anything but `en` / `tr`, or no cookie, is English.
// `timeZone` is UTC so no formatter ever uses the server's or the browser's zone.

import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { LOCALE_COOKIE, toLocale, type Locale } from "./locale";

export async function loadMessages(locale: Locale) {
  return (await import(`../../messages/${locale}.json`)).default;
}

export async function requestConfiguration() {
  const store = await cookies();
  const locale = toLocale(store.get(LOCALE_COOKIE)?.value);
  return {
    locale,
    messages: await loadMessages(locale),
    timeZone: "UTC",
  };
}

export default getRequestConfig(requestConfiguration);
