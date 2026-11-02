import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { GeoSwitches } from "@/components/geo/GeoSwitches";

export default function Loading() {
  const t = useTranslations("geo.page");
  return (
    <BoundaryLoading
      title={t("title")}
      what={t("loadingWhat")}
      description={t("description")}
      actions={<GeoSwitches window="24h" direction="SOURCE" />}
    />
  );
}
