// The range control of `/behavior/waf`: plain links, so the range is in the URL
// and the view is shareable. The active option carries `aria-current`.

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { WAF_RANGES, wafRangeLabel, type WafRangeKey } from "./wafRange";

export function WafRangeSelector({ active }: { active: WafRangeKey }) {
  const t = useTranslations("waf.range");
  const i18n = useBehaviorI18n();
  return (
    <nav className="range-selector" aria-label={t("label")}>
      <span className="muted">{t("title")}</span>
      {WAF_RANGES.map((r) => (
        <Link
          key={r.key}
          href={`/behavior/waf?range=${r.key}`}
          className={`range-option${r.key === active ? " range-option-active" : ""}`}
          aria-current={r.key === active ? "page" : undefined}
        >
          {wafRangeLabel(r, i18n)}
        </Link>
      ))}
    </nav>
  );
}
