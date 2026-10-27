import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("cadence.page");
  return <BoundaryLoading title={t("title")} what={t("loadingWhat")} />;
}
