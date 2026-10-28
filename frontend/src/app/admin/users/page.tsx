import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { UsersPage } from "./UsersPage";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("users.meta");
  return { title: t("title") };
}

function Page() {
  return <UsersPage />;
}

export default withRouteMessages("/admin/users", Page);
