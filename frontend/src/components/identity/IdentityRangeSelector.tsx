// The range control of the identity pages: plain links, so the range is in the URL and the view
// is shareable. The active option carries `aria-current`.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { RANGES, identityHref, type IdentityRange } from "./identityFormat";

export function IdentityRangeSelector({
  path,
  active,
  event = null,
}: {
  path: string;
  active: IdentityRange;
  event?: number | null;
}) {
  const t = useTranslations("identity.range");
  return (
    <nav className="range-selector" aria-label={t("label")}>
      <span className="muted">{t("title")}</span>
      {RANGES.map((range) => (
        <Link
          key={range}
          href={identityHref(path, { range, event })}
          className={`range-option${range === active ? " range-option-active" : ""}`}
          aria-current={range === active ? "page" : undefined}
        >
          {t(`option.${range}`)}
        </Link>
      ))}
    </nav>
  );
}
