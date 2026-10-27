"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("waf.boundary");
  return (
    <BoundaryError
      title={t("endpointsTitle")}
      what={t("endpointsError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
