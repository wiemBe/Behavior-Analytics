"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("propertyWatch");
  return <BoundaryError title={t("meta.detail")} what={t("boundary.detailWhatError")} digest={error.digest} onRetry={reset} />;
}
