"use client";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function DashboardLoading() {
  const t = useTranslations("dashboardBoundary");
  return <BoundaryLoading title={t("title")} what={t("loading")} offset="medium" />;
}
