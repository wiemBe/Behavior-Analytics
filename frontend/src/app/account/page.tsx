import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingState } from "@/components/ui/RouteState";

import { AccountHome } from "./AccountHome";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return (
    <Suspense fallback={<LoadingState what="the account" />}>
      <AccountHome />
    </Suspense>
  );
}
