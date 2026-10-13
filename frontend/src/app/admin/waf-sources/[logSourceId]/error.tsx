"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function WafSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="This WAF source" digest={error.digest} onRetry={reset} />;
}
