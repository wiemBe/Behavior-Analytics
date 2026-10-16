import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingState } from "@/components/ui/RouteState";

import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <Suspense fallback={<LoadingState what="the sign-in form" />}>
      <LoginForm />
    </Suspense>
  );
}
