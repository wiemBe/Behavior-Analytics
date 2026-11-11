"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function ReportsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("reports.boundary");
  return <BoundaryError title={t("title")} what={t("error")} digest={error.digest} onRetry={reset} />;
}
