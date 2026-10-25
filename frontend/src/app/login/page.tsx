import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoadingState } from "@/components/ui/RouteState";

import { LoginForm } from "./LoginForm";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("title") };
}

export default async function LoginPage() {
  const t = await getTranslations("login");
  return (
    <Suspense fallback={<LoadingState what={t("loading")} />}>
      <LoginForm />
    </Suspense>
  );
}
