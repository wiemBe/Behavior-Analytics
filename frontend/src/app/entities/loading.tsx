import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  const t = useTranslations("entities.boundary");
  return <BoundaryLoading title={t("title")} what={t("loading")} />;
}
