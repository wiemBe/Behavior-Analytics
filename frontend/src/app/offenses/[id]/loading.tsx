import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Loading() {
  const t = useTranslations("offenses.boundary");
  return (
    <>
      <p className="muted">
        <Link href="/offenses">{t("back")}</Link>
      </p>
      <BoundaryLoading title={t("detailTitle")} what={t("detailWhat")} />
    </>
  );
}
