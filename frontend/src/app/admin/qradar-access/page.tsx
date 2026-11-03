// Which QRadar APIs this site's token can reach, and what to ask the QRadar administrator for. The
// body reads from the browser: the check runs in the worker and the page polls its result. The page
// needs the `admin:*` permission.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { QRadarAccessPage } from "@/components/qradaraccess/QRadarAccessPage";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("qradarAccess.meta");
  return { title: t("title") };
}

async function Page() {
  const t = await getTranslations("qradarAccess.page");
  return (
    <>
      <PageHeader
        guide={guideFor("/admin/qradar-access")}
        title={t("title")}
        description={t("description")}
      />
      <QRadarAccessPage />
    </>
  );
}

export default withRouteMessages("/admin/qradar-access", Page);
