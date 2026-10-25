// The Home: the dashboard at `/`.
//
// The question: what needs me now? The first screen is the key figures and the top of the
// fleet charts. This is only the shell; the widgets are client components that read their
// own data, so the top bar's refresh reaches every one of them and a refused or failed
// read stays inside the widget that made it. Which dashboard is shown comes from the query
// (`?dashboard=`), which a client component reads, so it sits inside a Suspense boundary.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { HomeDashboard } from "@/components/home/HomeDashboard";
import { HomeFallback } from "@/components/home/HomeFallback";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("dashboard");
  return { title: t("title") };
}

export default function HomePage() {
  return (
    <Suspense fallback={<HomeFallback />}>
      <HomeDashboard />
    </Suspense>
  );
}
