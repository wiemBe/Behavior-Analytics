"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IpsSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("ips.bboundary");
  return (
    <BoundaryError
      title={t("sourceTitle")}
      what={t("sourceError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
