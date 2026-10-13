"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function WafSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="WAF source endpoints" digest={error.digest} onRetry={reset} />;
}
