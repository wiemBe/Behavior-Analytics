"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function DashboardError({
  error,
  reset,
}: {
  error?: Error & { digest?: string };
  reset?: () => void;
}) {
  const t = useTranslations("dashboardBoundary");
  return (
    <BoundaryError
      title={t("title")}
      what={t("error")}
      digest={error?.digest}
      onRetry={reset ?? (() => window.location.reload())}
    />
  );
}
