// The words that go with the shaded band on a volume chart (SOC-13d): the same UTC ranges and
// their effect state, so the band is never the only carrier of the information.

import { useTranslations } from "next-intl";

import type { TrainingExclusion } from "@/lib/api";
import { intervalText, isEffective, stateKey } from "@/lib/trainingExclusion";

import "./baseline.css";

/** The rows the chart shades: a pending or applied exclusion (a revoked or expired one is not drawn). */
export function effectiveSpans(rows: readonly TrainingExclusion[] | null) {
  return (rows ?? []).filter(isEffective).map((r) => ({ start: r.starts_at, end: r.ends_at }));
}

export function ExclusionBandText({ rows }: { rows: readonly TrainingExclusion[] | null }) {
  const t = useTranslations("trainingExclusion");
  const shown = (rows ?? []).filter(isEffective);
  if (shown.length === 0) return null;
  return (
    <p className="subtitle chart-note exclusion-text">
      {t("band.heading")}{" "}
      {shown
        .map((r) =>
          t("band.row", {
            interval: intervalText(r.starts_at, r.ends_at),
            state: t(stateKey(r) as never),
          }),
        )
        .join("; ")}
    </p>
  );
}
