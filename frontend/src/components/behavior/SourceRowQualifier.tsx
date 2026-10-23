// Inventory-row qualifier for a source whose evaluation is behind.
//
// The lifecycle state stays on the badge; this line must not fold the row into
// NORMAL. Times go through UtcTime. A retrying fault keeps "Not judged since"
// (T-170 §3). A given-up gap after evaluation has resumed names the missing
// bucket instead of claiming the watermark is the stall.

import type { SourceBehavior } from "@/lib/api";
import { rowQualifierModel } from "@/lib/evaluationFault";
import { UtcTime } from "@/components/ui/UtcTime";

export function SourceRowQualifier({ source }: { source: SourceBehavior }) {
  const model = rowQualifierModel(source);
  if (model == null) return null;
  if (model.kind === "retrying") {
    return (
      <span className="cell-sub">
        {model.judgedThrough ? (
          <>
            Not judged since <UtcTime value={model.judgedThrough} variant="compact" />
          </>
        ) : (
          "A bucket has no verdict"
        )}
        {model.givenUpSuffix ? ` · ${model.givenUpSuffix}` : null}
      </span>
    );
  }
  const { count, latestBucket } = model;
  if (count === 1) {
    return (
      <span className="cell-sub">
        {latestBucket ? (
          <>
            1 bucket not judged: <UtcTime value={latestBucket} variant="compact" />
          </>
        ) : (
          "1 bucket not judged"
        )}
      </span>
    );
  }
  if (count != null && count > 1) {
    return (
      <span className="cell-sub">
        {count.toLocaleString("en-US")} buckets not judged in 24 h
        {latestBucket ? (
          <>
            , latest <UtcTime value={latestBucket} variant="compact" />
          </>
        ) : null}
      </span>
    );
  }
  return (
    <span className="cell-sub">
      {latestBucket ? (
        <>
          bucket not judged: <UtcTime value={latestBucket} variant="compact" />
        </>
      ) : (
        "bucket not judged"
      )}
    </span>
  );
}
