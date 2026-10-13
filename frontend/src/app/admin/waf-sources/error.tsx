"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function WafSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="WAF sources" digest={error.digest} onRetry={reset} />;
}
