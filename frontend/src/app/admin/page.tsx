import type { Metadata } from "next";
import { useTranslations } from "next-intl";
import { getTranslations } from "next-intl/server";

import { UndecidedNotice } from "@/components/history/UndecidedNotice";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";

import { AdminIndex } from "./AdminIndex";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("admin.meta");
  return { title: t("title") };
}

function Page() {
  const t = useTranslations("admin.page");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <UndecidedNotice />
      <AdminIndex />
    </>
  );
}

export default withRouteMessages("/admin", Page);
