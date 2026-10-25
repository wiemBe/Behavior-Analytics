"use client";

// What the Home shows while the page's client code and its query string are read: one line of
// text, no skeleton. A client component so the line follows the interface language.

import { useTranslations } from "next-intl";

export function HomeFallback() {
  const t = useTranslations("dashboard");
  return (
    <p role="status" aria-live="polite">
      {t("loading")}
    </p>
  );
}
