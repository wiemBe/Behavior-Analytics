import { useTranslations } from "next-intl";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { WindowSwitch } from "@/components/fleet/WindowSwitch";

export default function Loading() {
  const t = useTranslations("fleet.page");
  return (
    <BoundaryLoading
      title={t("title")}
      what={t("loadingWhat")}
      description={t("description")}
      actions={<WindowSwitch active="24h" />}
    />
  );
}
