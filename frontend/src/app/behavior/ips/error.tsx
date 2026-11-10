"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IpsBehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("ips.bboundary");
  return (
    <BoundaryError
      title={t("listTitle")}
      what={t("listError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
