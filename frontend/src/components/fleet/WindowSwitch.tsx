// The fleet window, as links: the window belongs in the URL so a view can be
// shared, and links need no script. `aria-current` marks the active window.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { FLEET_WINDOWS } from "@/lib/fleet";
import type { FleetWindow } from "@/lib/api";

export function WindowSwitch({ active }: { active: FleetWindow }) {
  const t = useTranslations("fleet.window");
  const units = useTranslations("units.range");
  return (
    <nav className="range-selector" aria-label={t("nav")}>
      <span className="muted">{t("label")}</span>
      {FLEET_WINDOWS.map((w) => (
        <Link
          key={w.value}
          href={`/fleet?window=${w.value}`}
          className={`range-option${w.value === active ? " range-option-active" : ""}`}
          aria-current={w.value === active ? "page" : undefined}
        >
          {units(w.value)}
        </Link>
      ))}
    </nav>
  );
}
