"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("users");
  return (
    <BoundaryLoading
      title={t("boundary.title")}
      what={t("boundary.loadingWhat")}
      description={t("page.description")}
    />
  );
}
