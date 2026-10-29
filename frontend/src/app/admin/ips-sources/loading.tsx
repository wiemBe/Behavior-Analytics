import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("ips.page");
  return <BoundaryLoading title={t("title")} what={t("boundaryWhat")} />;
}
