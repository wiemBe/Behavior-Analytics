// The small text link a page header shows to its guide section. The accessible name carries the
// section's title; the titles come from `guideLinks` (a few words each), not from the guide text,
// which the browser never receives.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { own } from "@/i18n/ownKey";
import { rawMessage } from "@/lib/guideText";

export function GuideLink({ section }: { section: string }) {
  const t = useTranslations("guideLinks");
  const title = own(rawMessage(t, "titles") as Record<string, string>, section);
  // A section the catalog does not know shows no link rather than a link to a 404.
  if (title === undefined) return null;
  return (
    <Link className="guide-link" href={`/guide/${section}`} aria-label={t("named", { topic: title })}>
      {t("label")}
    </Link>
  );
}
