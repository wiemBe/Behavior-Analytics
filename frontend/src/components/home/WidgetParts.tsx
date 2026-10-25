"use client";

// The parts the Home tables put inside a `WidgetFrame`: the qualifier list, the
// unmeasured dash and the time cell. The frame and its states are in `WidgetFrame`.

import { useTranslations } from "next-intl";

import { NOT_MEASURED } from "@/lib/behavior";
import { formatAgo } from "@/lib/home/format";
import type { AgeWords } from "@/lib/time";
import { UtcTime } from "@/components/ui/UtcTime";

import "./tables.css";

/** An unmeasured figure: the dash, with the reason for assistive technology. */
export function Unmeasured({ reason }: { reason?: string | null }) {
  return (
    <span className="muted" title={reason ?? undefined}>
      {NOT_MEASURED}
      {reason && <span className="sr-only"> {reason}</span>}
    </span>
  );
}

/**
 * An instant in UTC with its zone, as a `<time>`, and its age in muted text.
 * A thin wrapper over `UtcTime`: null prints a dash, not a date, and a missing
 * age is omitted rather than drawn beside the instant.
 */
export function When({ iso }: { iso: string | null }) {
  const t = useTranslations("home.parts");
  const format = useTranslations("format");
  if (!iso) return <Unmeasured reason={t("noTime")} />;
  const ago = formatAgo(iso, Date.now(), format as AgeWords);
  return (
    <span className="tw-when">
      <UtcTime value={iso} variant="compact" age={ago} ageClassName="tw-sub" />
    </span>
  );
}
