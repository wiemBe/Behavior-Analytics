import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { useTranslations } from "next-intl";

export default function Loading() {
  const t = useTranslations("rules.boundary");
  return (
    <>
      <p className="muted">
        <Link href="/rules">{t("detailBack")}</Link>
      </p>
      <BoundaryLoading title={t("detailTitle")} what={t("detailWhat")} />
    </>
  );
}
