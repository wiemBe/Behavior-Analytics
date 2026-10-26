"use client";

import Link from "next/link";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function RulesIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("rules.boundary");
  return (
    <>
      <p className="muted">
        <Link href="/rules">{t("detailBack")}</Link>
      </p>
      <BoundaryError title={t("detailTitle")} what={t("detailWhatError")} digest={error.digest} onRetry={reset} />
    </>
  );
}
