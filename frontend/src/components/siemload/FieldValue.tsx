"use client";

// One QRadar field. A field that was read shows its value; a field that was not read shows its
// state in words and the sentence that says why, and never a number, a zero or a healthy look.

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import type { QRadarField } from "@/lib/api";

const TONE = { NOT_COLLECTED: "", FORBIDDEN: "warn", FAILED: "crit" } as const;

export function FieldGap({ field }: { field: QRadarField<unknown> }) {
  const t = useTranslations("siemLoad.field");
  // An OK field without a value is a field that was not read: it never reaches the screen as OK.
  const state = field.state === "OK" ? "NOT_COLLECTED" : field.state;
  return (
    <span className="sl-gap">
      <span className={`pill ${TONE[state]}`.trim()}>{t(state)}</span>
      <span className="sl-reason">{field.reason?.trim() || t("noValue")}</span>
      {state === "FORBIDDEN" ? (
        <Link href="/admin/qradar-access">{t("seeAccess")}</Link>
      ) : null}
    </span>
  );
}

export function FieldValue<T>({
  field,
  children,
}: {
  field: QRadarField<T>;
  children: (value: T) => ReactNode;
}) {
  if (field.state === "OK" && field.value !== null && field.value !== undefined) {
    return <>{children(field.value)}</>;
  }
  return <FieldGap field={field} />;
}
