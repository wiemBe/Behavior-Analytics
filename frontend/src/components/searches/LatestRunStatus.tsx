// The latest stored run, in words. A truncated count is a lower bound.
// A missing count is not drawn as zero.

import { runReadout } from "@/components/searches/authoring";
import type { SearchExecution } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "./searches.css";

export function LatestRunStatus({
  execution,
  consecutiveFailures,
}: {
  execution: SearchExecution;
  consecutiveFailures: number | null;
}) {
  const { t, locale } = useBehaviorI18n();
  const readout = runReadout(execution, consecutiveFailures, t, locale);
  const tone =
    readout.tone === "neutral"
      ? "banner latest-run search-run-note"
      : `banner ${readout.tone} latest-run search-run-note`;
  return (
    <div className={tone} role="status">
      <p>{readout.text}</p>
    </div>
  );
}
