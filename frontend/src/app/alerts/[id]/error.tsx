"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function AlertsIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("alerts.boundary");
  return <BoundaryError title={t("detailTitle")} what={t("detailWhatError")} digest={error.digest} onRetry={reset} />;
}
