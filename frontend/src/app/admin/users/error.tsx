"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function UsersError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("users.boundary");
  return (
    <BoundaryError
      title={t("title")}
      what={t("errorWhat")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
