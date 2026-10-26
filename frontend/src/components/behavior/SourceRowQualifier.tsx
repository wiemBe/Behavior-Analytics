// Inventory-row qualifier for a source whose evaluation is behind.
//
// The lifecycle state stays on the badge; this line must not fold the row into
// NORMAL. Times go through UtcTime. A retrying fault keeps "Not judged since"
// (T-170 §3). A given-up gap after evaluation has resumed names the missing
// bucket instead of claiming the watermark is the stall.

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import type { SourceBehavior } from "@/lib/api";
import { rowQualifierModel } from "@/lib/evaluationFault";
import { UtcTime } from "@/components/ui/UtcTime";
import { grouped } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

/** A rich-text tag that renders `node`. */
function slot(node: ReactNode): () => ReactNode {
  return function Slot() {
    return node;
  };
}

export function SourceRowQualifier({ source }: { source: SourceBehavior }) {
  const t = useTranslations("behavior.qualifier");
  const i18n = useBehaviorI18n();
  const model = rowQualifierModel(source, i18n);
  if (model == null) return null;
  const time = (value: string) => slot(<UtcTime value={value} variant="compact" />);
  if (model.kind === "retrying") {
    return (
      <span className="cell-sub">
        {model.judgedThrough
          ? t.rich("notJudgedSince", { time: time(model.judgedThrough) })
          : t("noVerdict")}
        {model.givenUpSuffix ? ` · ${model.givenUpSuffix}` : null}
      </span>
    );
  }
  const { count, latestBucket } = model;
  if (count === 1) {
    return (
      <span className="cell-sub">
        {latestBucket ? t.rich("oneWithTime", { time: time(latestBucket) }) : t("one")}
      </span>
    );
  }
  if (count != null && count > 1) {
    return (
      <span className="cell-sub">
        {t("many", { count, shown: grouped(count, i18n.locale) })}
        {latestBucket ? t.rich("latest", { time: time(latestBucket) }) : null}
      </span>
    );
  }
  return (
    <span className="cell-sub">
      {latestBucket ? t.rich("unitWithTime", { time: time(latestBucket) }) : t("unit")}
    </span>
  );
}
