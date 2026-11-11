// How did the period go: alerts and anomalies opened, time to act, noise by detector. The period
// is in the URL (`?period=`, and `&start=` for a calendar period other than the current one).

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ReportsView } from "@/components/reports/ReportsView";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { guideFor } from "@/lib/guideRoutes";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("reports");
  return { title: t("meta") };
}

const first = (value: string | string[] | undefined): string | undefined =>
  Array.isArray(value) ? value[0] : value;

async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("reports");
  const params = await searchParams;
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} guide={guideFor("/reports")} />
      <ReportsView period={first(params.period)} start={first(params.start)} />
    </>
  );
}

export default withRouteMessages("/reports", ReportsPage);
