"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function WafBehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="WAF behavior" digest={error.digest} onRetry={reset} />;
}
