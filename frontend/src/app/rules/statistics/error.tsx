"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function RuleStatisticsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("rules");
  return (
    <BoundaryError
      title={t("statistics.title")}
      what={t("boundary.statisticsWhatError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
