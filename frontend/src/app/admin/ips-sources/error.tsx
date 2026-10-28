"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IpsSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("ips.page");
  return <BoundaryError title={t("title")} what={t("boundaryWhat")} digest={error.digest} onRetry={reset} />;
}
