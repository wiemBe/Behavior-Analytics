"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("behavior.boundary");
  return <BoundaryLoading title={t("title")} what={t("loadingWhat")} offset="small" />;
}
