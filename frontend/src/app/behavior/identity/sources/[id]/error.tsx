"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IdentitySourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("identity.boundary");
  return (
    <BoundaryError
      title={t("sourceTitle")}
      what={t("sourceError")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
