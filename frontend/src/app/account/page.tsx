import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoadingState } from "@/components/ui/RouteState";

import { AccountHome } from "./AccountHome";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("account");
  return { title: t("title") };
}

export default function AccountPage() {
  // The client state uses the same translated phrase after hydration.
  return (
    <Suspense fallback={<AccountLoading />}>
      <AccountHome />
    </Suspense>
  );
}

async function AccountLoading() {
  const t = await getTranslations("account");
  return <LoadingState what={t("loading")} />;
}
