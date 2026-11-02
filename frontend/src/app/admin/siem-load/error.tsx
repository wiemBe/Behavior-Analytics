"use client";

import { useTranslations } from "next-intl";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

// The title and the noun are the navigation label, a shell string, so this boundary needs no key of
// its own in the root provider.
export default function SiemLoadError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("shell.nav");
  return (
    <BoundaryError
      title={t("siemLoad")}
      what={t("siemLoad")}
      digest={error.digest}
      onRetry={reset}
    />
  );
}
