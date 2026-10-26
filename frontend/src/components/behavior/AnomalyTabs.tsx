"use client";

// The investigation's three secondary panels.
//
// A thin client wrapper so the page itself stays a server component: the
// panels are rendered on the server and passed in as props, and only the tab
// mechanics — selection, arrow-key movement, focus — need to run in the
// browser.

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Tabs } from "@/components/ui/Tabs";

export function AnomalyTabs({
  evidence,
  lifecycle,
  technical,
}: {
  evidence: ReactNode;
  lifecycle: ReactNode;
  technical: ReactNode;
}) {
  const t = useTranslations("anomalies.tabs");
  return (
    <Tabs
      label={t("title")}
      tabs={[
        // Evidence first: "what changed" is the question the page exists to
        // answer, and the other two panels support that answer.
        { id: "evidence", label: t("evidence"), content: () => evidence },
        { id: "lifecycle", label: t("lifecycle"), content: () => lifecycle },
        { id: "technical", label: t("technical"), content: () => technical },
      ]}
    />
  );
}
