"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("customRules.boundary");
  return <BoundaryError title={t("newTitle")} what={t("newWhatError")} digest={error.digest} onRetry={reset} />;
}
