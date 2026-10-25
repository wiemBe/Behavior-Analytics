"use client";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Loading() {
  const t = useTranslations("login");
  return <BoundaryLoading title={t("title")} what={t("loading")} />;
}
