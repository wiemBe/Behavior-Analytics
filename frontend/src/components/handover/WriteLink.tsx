"use client";

// The page header's one action. Present only for an account that holds `anomaly:triage`.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useWriteAccess } from "./access";

export function WriteLink() {
  const t = useTranslations("handover");
  if (useWriteAccess() !== "yes") return null;
  return (
    <Link className="action secondary button-link" href="/handover?compose=1">
      {t("write")}
    </Link>
  );
}
