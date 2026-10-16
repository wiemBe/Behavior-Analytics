import type { Metadata } from "next";
import { Suspense } from "react";

import { PasswordChange } from "@/components/auth/PasswordChange";
import { LoadingState } from "@/components/ui/RouteState";

export const metadata: Metadata = { title: "Change password" };

export default function PasswordPage() {
  return (
    <Suspense fallback={<LoadingState what="the password form" />}>
      <PasswordChange />
    </Suspense>
  );
}
