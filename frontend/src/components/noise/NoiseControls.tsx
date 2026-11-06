// The controls of `/behavior/noise`: plain links, so every choice is in the URL and the screen can
// be shared. The chosen option carries `aria-current`. A new window goes back to the first page.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { NOISE_WINDOWS, noiseHref, type NoiseQuery } from "@/lib/noise";

import "./noise.css";

export function NoiseControls({ query }: { query: NoiseQuery }) {
  const t = useTranslations("noise.controls");
  return (
    <div className="noise-controls">
      <nav className="range-selector" aria-label={t("windowLabel")}>
        <span className="muted">{t("window")}</span>
        {NOISE_WINDOWS.map((days) => {
          const active = days === query.window;
          return (
            <Link
              key={days}
              href={noiseHref(query, { window: days, offset: 0 })}
              className={`range-option${active ? " range-option-active" : ""}`}
              aria-current={active ? "page" : undefined}
            >
              {t("windowOption", { days })}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
