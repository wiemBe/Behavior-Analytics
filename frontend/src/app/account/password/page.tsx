import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { PasswordChange } from "@/components/auth/PasswordChange";
import { LoadingState } from "@/components/ui/RouteState";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("password");
  return { title: t("title") };
}

export default async function PasswordPage() {
  const t = await getTranslations("password");
  return (
    <Suspense fallback={<LoadingState what={t("loading")} />}>
      <PasswordChange />
    </Suspense>
  );
}
