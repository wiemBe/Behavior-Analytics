"use client";

// The app router when one is mounted, else null. `next/navigation`'s own `useRouter` throws
// outside the app router; the list keys and the `g` sequence fall back to a plain navigation
// instead, so a component that is rendered without a router (a test, an error boundary) still
// mounts.

import { useContext } from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

export function useSafeRouter() {
  return useContext(AppRouterContext);
}
