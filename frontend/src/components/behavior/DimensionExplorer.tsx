"use client";

// The evidence tab's dimension selector.
//
// Replaces ten contributor tables rendered one after another — 26 tables and
// 125 column headers on the live Phase A anomaly — with one table at a time
// and a list showing what every dimension's status was.
//
// The list is the coverage statement, and it must be complete. A dimension
// that was never collected stays visible with its status, because reading down
// the list is how an analyst learns which fields the conclusion actually rests
// on. A hidden UNAVAILABLE dimension would read as having been checked and
// found clean.

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ContributorTable } from "@/components/behavior/ContributorTable";
import type { ExplanationDimension } from "@/lib/api";
import { dimensionLabel, dimensionTone } from "@/lib/behavior";
import { availabilityCode } from "@/lib/behaviorI18n";
import { defaultDimension, dimensionPriority } from "@/lib/contributors";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

export function DimensionExplorer({
  dimensions,
  evidenceStatus,
}: {
  dimensions: ExplanationDimension[];
  /** The incident's evidence status; a failure is stated in full once, by the banner. */
  evidenceStatus?: string;
}) {
  const t = useTranslations("anomalies.dimensions");
  const i18n = useBehaviorI18n();
  const ordered = [...dimensions].sort(
    (a, b) => dimensionPriority(a.dimension) - dimensionPriority(b.dimension),
  );
  const initial = defaultDimension(ordered);
  const [selected, setSelected] = useState<string | null>(initial?.dimension ?? null);

  if (ordered.length === 0) {
    if (evidenceStatus === "FAILED") {
      return <div className="notice">{t("failed")}</div>;
    }
    return <div className="notice">{t("none")}</div>;
  }

  const active =
    ordered.find((d) => d.dimension === selected) ?? initial ?? ordered[0];

  const unchecked = ordered.filter(
    (d) => d.availability === "UNAVAILABLE" || d.availability === "FAILED",
  );

  return (
    <div className="dimension-explorer">
      <div className="dimension-list">
        <h4 className="sr-only">{t("title")}</h4>
        <ul>
          {ordered.map((d) => {
            const current = d.dimension === active.dimension;
            return (
              <li key={d.dimension}>
                <button
                  type="button"
                  className={`dimension-button${current ? " dimension-button-active" : ""}`}
                  // The selected dimension is a current item within the list,
                  // announced rather than only highlighted.
                  aria-current={current ? "true" : undefined}
                  onClick={() => setSelected(d.dimension)}
                >
                  <span className="dimension-button-label">
                    {dimensionLabel(d.dimension, i18n)}
                  </span>
                  <span className={`pill pill-quiet ${dimensionTone(d.availability)}`}>
                    {availabilityCode(i18n, d.availability)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        {unchecked.length > 0 && (
          <p className="metric-note">
            {t("unchecked", {
              count: unchecked.length,
              labels: unchecked.map((d) => dimensionLabel(d.dimension, i18n)).join(", "),
            })}
          </p>
        )}
      </div>

      <div className="dimension-detail">
        <ContributorTable key={active.dimension} dimension={active} />
      </div>
    </div>
  );
}
