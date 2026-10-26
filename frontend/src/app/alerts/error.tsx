"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function AlertsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("alerts");
  return <BoundaryError title={t("title")} what={t("boundary.listWhatError")} digest={error.digest} onRetry={reset} />;
}
