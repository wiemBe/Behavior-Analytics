import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Loading() {
  const t = useTranslations("customRules.boundary");
  return <BoundaryLoading title={t("detailTitle")} what={t("detailWhat")} />;
}
