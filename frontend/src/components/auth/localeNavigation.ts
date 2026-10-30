// After sign-in the account language is already in `ba_locale`. A client
// navigation reuses the language the current page rendered, so the first page
// stays in that language until a reload. A full document load is how the first
// page is rendered in the account language. When the cookie already matches
// the rendered language, a client navigation is enough: that is also what a
// same-browser language save relies on, and this helper is not that path.

import { readLocaleCookie, type Locale } from "@/i18n/locale";
import { sessionNavigation } from "@/lib/api";

export function continueInAccountLanguage(
  rendered: Locale,
  destination: string,
  replace: (url: string) => void,
): void {
  if (readLocaleCookie() !== rendered) {
    sessionNavigation.assign(destination);
    return;
  }
  replace(destination);
}
