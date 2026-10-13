// The range control of `/behavior/waf`: plain links, so the range is in the URL
// and the view is shareable. The active option carries `aria-current`.

import Link from "next/link";

import { WAF_RANGES, type WafRangeKey } from "./wafRange";

export function WafRangeSelector({ active }: { active: WafRangeKey }) {
  return (
    <nav className="range-selector" aria-label="Admission overflow range">
      <span className="muted">Range</span>
      {WAF_RANGES.map((r) => (
        <Link
          key={r.key}
          href={`/behavior/waf?range=${r.key}`}
          className={`range-option${r.key === active ? " range-option-active" : ""}`}
          aria-current={r.key === active ? "page" : undefined}
        >
          {r.label}
        </Link>
      ))}
    </nav>
  );
}
