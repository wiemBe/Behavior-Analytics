// The source page's evaluation-fault notice: retrying, given up, or none.
//
// A transient fault that has already cleared has no notice; that line lives in
// the advanced section. Times go through UtcTime. The copy names the bucket
// that has no verdict and never draws a zero for it.

import { useTranslations } from "next-intl";
import { Fragment, type ReactNode } from "react";

import { UtcTime } from "@/components/ui/UtcTime";
import type { SourceBehavior } from "@/lib/api";
import {
  attemptsFailed,
  attemptsUsed,
  givenUpNotice,
  retryingNotice,
} from "@/lib/evaluationFault";
import { grouped } from "@/lib/figureText";
import { errorClassText } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

function BucketTime({ value }: { value: string }) {
  return <UtcTime value={value} variant="compact" />;
}

/** The sentences of one notice, one space between them. */
function Sentences({ parts }: { parts: ReactNode[] }) {
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 ? " " : null}
          {part}
        </Fragment>
      ))}
    </>
  );
}

export function SourceEvaluationNotice({ source }: { source: SourceBehavior }) {
  const t = useTranslations("behavior.evaluationNotice");
  const i18n = useBehaviorI18n();
  const retrying = retryingNotice(source);
  if (retrying) {
    const used = attemptsUsed(retrying.attempts, retrying.maxAttempts, i18n);
    const { lastFailure, judgedThrough } = retrying;
    const parts: ReactNode[] = [
      t.rich("noVerdict", { bucket: () => <BucketTime value={retrying.failedBucket} /> }),
      lastFailure
        ? t.rich("failedAt", {
            time: () => <BucketTime value={lastFailure} />,
            errorClass: errorClassText(retrying.errorClass),
          })
        : t("failed"),
    ];
    if (used) parts.push(`${used}.`);
    parts.push(
      judgedThrough
        ? t.rich("coverThrough", { time: () => <BucketTime value={judgedThrough} /> })
        : t("nextAttempt"),
    );
    return (
      <div className="notice" role="status">
        <Sentences parts={parts} />
      </div>
    );
  }

  const given = givenUpNotice(source);
  if (given) {
    const bucket = () => <BucketTime value={given.failedBucket} />;
    const phrase = given.attempts != null ? attemptsFailed(given.attempts, i18n) : null;
    const errorClass = errorClassText(given.errorClass);
    const last = given.lastFailure ? () => <BucketTime value={given.lastFailure as string} /> : null;
    const first = t.rich(
      phrase != null
        ? last
          ? "notJudgedAttemptsLast"
          : "notJudgedAttempts"
        : last
          ? "notJudgedLast"
          : "notJudged",
      { bucket, phrase: phrase ?? "", time: last ?? (() => null), errorClass },
    );
    const parts: ReactNode[] = [first];
    if (given.skipped === 1) parts.push(t("skippedOne"));
    else if (given.skipped != null && given.skipped > 1) {
      parts.push(
        t("skippedMany", { count: given.skipped, shown: grouped(given.skipped, i18n.locale) }),
      );
    }
    const resumedAt = given.judgedThrough;
    if (given.resumed && resumedAt) {
      parts.push(t.rich("resumed", { time: () => <BucketTime value={resumedAt} /> }));
    }
    return (
      <div className="notice" role="status">
        <Sentences parts={parts} />
      </div>
    );
  }

  return null;
}
