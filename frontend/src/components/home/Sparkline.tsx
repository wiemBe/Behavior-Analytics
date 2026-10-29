// The Total EPS card's trend line (UR.4.2): a neutral stroke, no fill, no marker.
//
// Inline SVG, as HealthDistribution is, so its width is the card's and its
// stroke is a token. A step with no measurement is a gap in the line, never a
// point at zero (guidelines §4). The line is context for the headline figure;
// its figures are written beneath it and it is hidden from assistive
// technology, which gets the same facts as text.

import { useTranslations } from "next-intl";

import { useMeasures } from "@/i18n/measures";
import type { FleetEpsSeries } from "@/lib/api";
import { sparkline } from "@/lib/home/sparkline";
import { useWidgetCopy } from "@/widgets/copy";

import "./kpi.css";

const W = 240;
const H = 32;

export function Sparkline({ series }: { series: FleetEpsSeries }) {
  const t = useTranslations("home.sparkline");
  const measures = useMeasures();
  const copy = useWidgetCopy();
  const points = series.series[0]?.points ?? [];
  const g = sparkline(
    points.map((p) => p.eps),
    W,
    H,
  );
  const { steps_measured: have, steps_total: total } = series.coverage;
  const windowText = copy.range(series.window);
  const text =
    g.measured === 0
      ? t("none", { window: windowText, total: measures.count(total) })
      : t("summary", {
          window: windowText,
          have: measures.count(have),
          total: measures.count(total),
          peak: measures.eps(g.max as number),
        });

  // With no measured point there is no trend to draw. Even an empty axis rule
  // would read as a zero line, while the card already explains the missing data.
  if (g.measured === 0) return null;

  return (
    <figure className="kpi-spark" title={text}>
      <svg
        className="kpi-spark-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        {g.path ? <path className="kpi-spark-line" d={g.path} /> : null}
      </svg>
      <figcaption className="sr-only">{text}</figcaption>
    </figure>
  );
}
