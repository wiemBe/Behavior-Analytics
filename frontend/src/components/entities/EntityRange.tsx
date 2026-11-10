// The window control of the entity timeline: three plain links, so the range is in the address.
// A link keeps the value and the kind and drops the cursor (a cursor belongs to one window). The
// absolute window the API answered for is printed beside it, in UTC (guidelines §10).

import Link from "next/link";
import { useTranslations } from "next-intl";

import { UtcTime } from "@/components/ui/UtcTime";
import { ENTITY_RANGES, entityHref, type EntityKindParam, type EntityRangeKey } from "@/lib/entities";

export function EntityRange({
  value,
  kind,
  active,
  window: shown,
}: {
  value: string;
  kind: EntityKindParam | "";
  active: EntityRangeKey;
  /** The window the answer covers; null before there is one. */
  window: { start: string; end: string } | null;
}) {
  const t = useTranslations("entities.range");
  return (
    <div className="entity-range">
      <nav className="range-selector" aria-label={t("label")}>
        <span className="muted">{t("title")}</span>
        {ENTITY_RANGES.map((r) => (
          <Link
            key={r.key}
            href={entityHref({ value, kind, range: r.key })}
            className={`range-option${r.key === active ? " range-option-active" : ""}`}
            aria-current={r.key === active ? "page" : undefined}
          >
            {t(`options.${r.key}`)}
          </Link>
        ))}
      </nav>
      {shown && (
        <p className="entity-window muted">
          {t.rich("window", {
            from: () => <UtcTime value={shown.start} />,
            to: () => <UtcTime value={shown.end} />,
          })}
        </p>
      )}
    </div>
  );
}
