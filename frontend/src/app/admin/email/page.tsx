// What the alert e-mail will look like, who gets it, and whether it reaches them. The body reads
// from the browser: every part is its own read, the editor keeps drafts, and the page needs the
// `admin:notifications` permission.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { EmailAdminPage } from "@/components/mailadmin/EmailAdminPage";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("mailAdmin.meta");
  return { title: t("title") };
}

async function Page() {
  const t = await getTranslations("mailAdmin.page");
  return (
    <>
      <PageHeader
        guide={guideFor("/admin/email")}
        title={t("title")}
        description={t("description")}
      />
      <EmailAdminPage />
    </>
  );
}

export default withRouteMessages("/admin/email", Page);
