// What has been read from QRadar's past, and what can still be. The page reads the status from
// the browser: the decision block changes it in place.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { useTranslations } from "next-intl";

import { HistoryBackfillPage } from "@/components/history/HistoryBackfillPage";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("history.meta");
  return { title: t("title") };
}

export default function Page() {
  const t = useTranslations("history.page");
  return (
    <>
      <PageHeader guide={guideFor("/admin/history-backfill")} title={t("title")} description={t("description")} />
      <HistoryBackfillPage />
    </>
  );
}
