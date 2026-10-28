import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoadingState } from "@/components/ui/RouteState";
import { withRouteMessages } from "@/i18n/RouteMessages";

import { LoginForm } from "./LoginForm";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("title") };
}

async function LoginPage() {
  const t = await getTranslations("login");
  return (
    <Suspense fallback={<LoadingState what={t("loading")} />}>
      <LoginForm />
    </Suspense>
  );
}

export default withRouteMessages("/login", LoginPage);
