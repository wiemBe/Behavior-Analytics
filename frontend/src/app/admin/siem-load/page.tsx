// How much load the platform puts on QRadar now, and how QRadar itself is doing. The body reads
// from the browser so the top bar's Refresh can re-read it; the page only reads the summary window
// out of the URL.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { SiemLoadPage } from "@/components/siemload/SiemLoadPage";
import { parseRange } from "@/components/siemload/siemFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("siemLoad.meta");
  return { title: t("title") };
}

async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const t = await getTranslations("siemLoad.page");
  const params = await searchParams;
  return (
    <>
      <PageHeader
        guide={guideFor("/admin/siem-load")}
        title={t("title")}
        description={t("description")}
      />
      <SiemLoadPage range={parseRange(params.range)} />
    </>
  );
}

export default withRouteMessages("/admin/siem-load", Page);
