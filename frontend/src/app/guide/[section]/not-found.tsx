// An address the guide has no section for: says so and returns to the contents.

import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";

export default async function GuideSectionNotFound() {
  const t = await getTranslations("guide");
  return (
    <>
      <PageHeader title={t("ui.notFoundTitle")} description={t("ui.notFoundBody")} />
      <p>
        <Link href="/guide">{t("ui.allTopics")}</Link>
      </p>
    </>
  );
}
