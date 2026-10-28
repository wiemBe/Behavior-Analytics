import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { PasswordChange } from "@/components/auth/PasswordChange";
import { LoadingState } from "@/components/ui/RouteState";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("password");
  return { title: t("title") };
}

async function PasswordPage() {
  const t = await getTranslations("password");
  return (
    <Suspense fallback={<LoadingState what={t("loading")} />}>
      <PasswordChange />
    </Suspense>
  );
}

export default withRouteMessages("/account/password", PasswordPage);
