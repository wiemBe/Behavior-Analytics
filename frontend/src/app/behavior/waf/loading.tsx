"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("waf.boundary");
  return (
    <BoundaryLoading
      title={t("behaviorTitle")}
      what={t("behaviorLoading")}
      offset="small"
    />
  );
}
