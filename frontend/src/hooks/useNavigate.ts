"use client";

// Client-side navigation that degrades to a plain page load.
//
// `useRouter` from `next/navigation` throws when no app router is mounted, which is the case for
// a page rendered on its own (the Vitest server-page renders). Reading the router from its
// context instead returns null there, and the filter bar then does what a form without script
// would do: load the address.

import { useContext } from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

export function useNavigate(): (href: string) => void {
  const router = useContext(AppRouterContext);
  return (href: string) => {
    if (router) router.push(href);
    else window.location.assign(href);
  };
}
