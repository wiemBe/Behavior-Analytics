// The source page's evaluation-fault notice: retrying, given up, or none.
//
// A transient fault that has already cleared has no notice; that line lives in
// the advanced section. Times go through UtcTime. The copy names the bucket
// that has no verdict and never draws a zero for it.

import type { SourceBehavior } from "@/lib/api";
import {
  attemptsUsed,
  givenUpNotice,
  retryingNotice,
} from "@/lib/evaluationFault";
import { UtcTime } from "@/components/ui/UtcTime";

function BucketTime({ value }: { value: string }) {
  return <UtcTime value={value} variant="compact" />;
}

export function SourceEvaluationNotice({ source }: { source: SourceBehavior }) {
  const retrying = retryingNotice(source);
  if (retrying) {
    const used = attemptsUsed(retrying.attempts, retrying.maxAttempts);
    return (
      <div className="notice" role="status">
        The <BucketTime value={retrying.failedBucket} /> bucket has no verdict.
        {retrying.lastFailure ? (
          <>
            {" "}
            Its evaluation failed at <BucketTime value={retrying.lastFailure} />
            {retrying.errorClass ? ` (${retrying.errorClass})` : ""}.
          </>
        ) : (
          " Its evaluation failed."
        )}
        {used ? ` ${used}.` : ""}
        {retrying.judgedThrough ? (
          <>
            {" "}
            Verdicts for this source cover buckets through{" "}
            <BucketTime value={retrying.judgedThrough} />; the next attempt runs
            at the next evaluation tick.
          </>
        ) : (
          " The next attempt runs at the next evaluation tick."
        )}
      </div>
    );
  }

  const given = givenUpNotice(source);
  if (given) {
    let skipped: string | null = null;
    if (given.skipped === 1) skipped = "1 bucket in this window has no verdict.";
    else if (given.skipped != null && given.skipped > 1) {
      skipped = `${given.skipped.toLocaleString("en-US")} buckets in this window have no verdict.`;
    }
    return (
      <div className="notice" role="status">
        The <BucketTime value={given.failedBucket} /> bucket was not judged
        {given.attempts != null
          ? `: ${given.attempts === 1 ? "its evaluation failed once" : `its evaluation failed ${given.attempts} times`}`
          : ""}
        {given.lastFailure ? (
          <>
            , most recently at <BucketTime value={given.lastFailure} />
            {given.errorClass ? ` (${given.errorClass})` : ""}
          </>
        ) : null}
        .{skipped ? ` ${skipped}` : ""}
        {given.resumed && given.judgedThrough ? (
          <>
            {" "}
            Evaluation resumed; verdicts now cover buckets through{" "}
            <BucketTime value={given.judgedThrough} />.
          </>
        ) : null}
      </div>
    );
  }

  return null;
}
