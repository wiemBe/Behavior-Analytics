import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DirectoryPage } from "./DirectoryPage";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("directory.meta");
  return { title: t("title") };
}

function Page() {
  return <DirectoryPage />;
}

export default withRouteMessages("/admin/directory", Page);
