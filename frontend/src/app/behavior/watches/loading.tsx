"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("propertyWatch");
  return <BoundaryLoading title={t("meta.list")} what={t("boundary.listWhat")} />;
}
