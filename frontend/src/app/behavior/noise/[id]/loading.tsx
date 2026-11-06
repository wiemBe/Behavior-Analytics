"use client";

import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("noise.detail.boundary");
  return <BoundaryLoading title={t("title")} what={t("loading")} offset="small" />;
}
