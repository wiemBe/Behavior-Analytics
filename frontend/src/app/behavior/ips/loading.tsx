"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("ips.bboundary");
  return <BoundaryLoading title={t("listTitle")} what={t("listLoading")} offset="small" />;
}
