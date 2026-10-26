import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Loading() {
  const t = useTranslations("offenses");
  return <BoundaryLoading title={t("title")} what={t("boundary.listWhat")} offset="small" />;
}
