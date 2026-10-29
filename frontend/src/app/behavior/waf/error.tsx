"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafBehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("waf.boundary");
  return (
    <BoundaryError
      title={t("behaviorTitle")}
      what={t("behaviorError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
