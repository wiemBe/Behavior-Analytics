"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function LoginError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("login");
  return <BoundaryError title={t("title")} what={t("title")} digest={error.digest} onRetry={reset} />;
}
