// Which alerts leave the site, and where the webhooks go. The body reads from the browser: the
// rules and the destinations are two reads that fail apart, and the page needs the
// `admin:notifications` permission.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { NotificationRoutingPage } from "@/components/notifyrouting/NotificationRoutingPage";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("notifyRouting.meta");
  return { title: t("title") };
}

async function Page() {
  const t = await getTranslations("notifyRouting.page");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <NotificationRoutingPage />
    </>
  );
}

export default withRouteMessages("/admin/notifications", Page);
