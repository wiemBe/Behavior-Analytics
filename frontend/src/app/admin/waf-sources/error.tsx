"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("waf.boundary");
  return (
    <BoundaryError
      title={t("adminTitle")}
      what={t("adminError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
