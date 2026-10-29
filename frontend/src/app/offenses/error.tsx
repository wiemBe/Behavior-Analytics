"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function OffensesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("offenses");
  return <BoundaryError title={t("title")} what={t("boundary.listWhatError")} digest={error.digest} onRetry={reset} />;
}
