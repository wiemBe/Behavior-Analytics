"use client";

// Whether the signed-in account may open Administration → Features, for a client component's
// notice. Until the session is read the answer is no.

import { useSession } from "@/components/auth/session";

import { canReadFeatures } from "./featuresRich";

export function useFeaturesLinked(): boolean {
  const session = useSession();
  return session.status === "ready" && canReadFeatures(session.me);
}
