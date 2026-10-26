"use client";

import Link from "next/link";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function OffensesIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("offenses.boundary");
  return (
    <>
      <p className="muted">
        <Link href="/offenses">{t("back")}</Link>
      </p>
      <BoundaryError
        title={t("detailTitle")}
        what={t("detailWhatError")}
        digest={error.digest}
        onRetry={reset}
      />
    </>
  );
}
