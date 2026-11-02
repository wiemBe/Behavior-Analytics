// The Geography page's two choices, as links: the address the country is taken from, and the
// window. Both belong in the URL so a view can be shared, and links need no script;
// `aria-current` marks the active one. Each link keeps the other choice.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { FLEET_WINDOWS } from "@/lib/fleet";
import type { FleetWindow, GeoDirection } from "@/lib/api";

import { DIRECTIONS } from "./geoModel";

export function GeoSwitches({
  window,
  direction,
}: {
  window: FleetWindow;
  direction: GeoDirection;
}) {
  const t = useTranslations("geo");
  const units = useTranslations("units.range");
  return (
    <div className="geo-switches">
      <nav className="range-selector" aria-label={t("controls.directionNav")}>
        <span className="muted">{t("controls.directionLabel")}</span>
        {DIRECTIONS.map((d) => (
          <Link
            key={d}
            href={`/geography?window=${window}&direction=${d}`}
            className={`range-option${d === direction ? " range-option-active" : ""}`}
            aria-current={d === direction ? "page" : undefined}
          >
            {t(`direction.${d}`)}
          </Link>
        ))}
      </nav>
      <nav className="range-selector" aria-label={t("controls.windowNav")}>
        <span className="muted">{t("controls.windowLabel")}</span>
        {FLEET_WINDOWS.map((w) => (
          <Link
            key={w.value}
            href={`/geography?window=${w.value}&direction=${direction}`}
            className={`range-option${w.value === window ? " range-option-active" : ""}`}
            aria-current={w.value === window ? "page" : undefined}
          >
            {units(w.value)}
          </Link>
        ))}
      </nav>
    </div>
  );
}
