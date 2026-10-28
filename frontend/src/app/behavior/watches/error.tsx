"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("propertyWatch");
  return <BoundaryError title={t("meta.list")} what={t("boundary.listWhatError")} digest={error.digest} onRetry={reset} />;
}
