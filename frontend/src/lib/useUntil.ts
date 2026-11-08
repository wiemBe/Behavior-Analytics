"use client";

import { useTranslations } from "next-intl";

import { useNow } from "@/lib/useNow";
import { untilEnd } from "@/lib/suppression";

/** The "in 6 d" after an end time. Null until the browser clock is known. */
export function useUntil() {
  const t = useTranslations("suppression.until");
  const now = useNow();
  return (endsAt: string | null | undefined): string | null => {
    if (now === null) return null;
    const until = untilEnd(endsAt, now);
    if (until === null) return null;
    if (until.unit === "past") return t("past");
    if (until.unit === "underMinute") return t("underMinute");
    return t(until.unit, { count: until.count });
  };
}
