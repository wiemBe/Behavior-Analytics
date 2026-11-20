// Which families collect and alert, where each value comes from, and what each costs. The body
// reads from the browser, so a change shows the API's answer. It needs `read:features`.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { FeaturesPage } from "@/components/features/FeaturesPage";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("features.meta");
  return { title: t("title") };
}

async function Page() {
  const t = await getTranslations("features.page");
  return (
    <>
      <PageHeader guide={guideFor("/admin/features")} title={t("title")} />
      <FeaturesPage />
    </>
  );
}

export default withRouteMessages("/admin/features", Page);
