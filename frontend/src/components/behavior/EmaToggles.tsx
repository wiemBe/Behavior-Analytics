"use client";

// The EMA overlay control: a labelled group of toggle buttons, one per period,
// and the per-viewer memory of which are on. Reusable by any time-series chart.

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import { EMA_PERIODS, type EmaPeriod } from "@/lib/ema";

/** The browser's remembered choice. Per browser; a missing or blocked
 *  localStorage, or anything else in the slot, leaves the default: all off. */
export const EMA_STORAGE_KEY = "qradar-dash.chart.ema";

function readStored(): EmaPeriod[] {
  try {
    const raw = window.localStorage.getItem(EMA_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return EMA_PERIODS.filter((p) => parsed.includes(p));
  } catch {
    return [];
  }
}

function writeStored(periods: EmaPeriod[]) {
  try {
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify(periods));
  } catch {
    // The choice then lasts for this page view only.
  }
}

/**
 * The enabled periods, sorted. The server render and the first client render
 * are both "all off"; the remembered choice is restored after mount, so the
 * two cannot disagree.
 */
export function useEmaPeriods(): [EmaPeriod[], (period: EmaPeriod) => void] {
  const [enabled, setEnabled] = useState<EmaPeriod[]>([]);

  useEffect(() => {
    const stored = readStored();
    if (stored.length > 0) setEnabled(stored);
  }, []);

  const toggle = useCallback((period: EmaPeriod) => {
    setEnabled((current) => {
      const next = EMA_PERIODS.filter((p) =>
        p === period ? !current.includes(p) : current.includes(p),
      );
      writeStored(next);
      return next;
    });
  }, []);

  return [enabled, toggle];
}

export function EmaToggles({
  enabled,
  onToggle,
}: {
  enabled: EmaPeriod[];
  onToggle: (period: EmaPeriod) => void;
}) {
  const t = useTranslations("behavior.chart.ema");
  return (
    <div className="chart-tools" role="group" aria-label={t("group")}>
      <span className="chart-tools-label" aria-hidden="true">
        {t("label")}
      </span>
      <span className="range-chip-list">
        {EMA_PERIODS.map((period) => (
          <button
            key={period}
            type="button"
            className="range-chip"
            aria-pressed={enabled.includes(period)}
            aria-label={t("toggle", { period })}
            onClick={() => onToggle(period)}
          >
            {period}
          </button>
        ))}
      </span>
    </div>
  );
}
