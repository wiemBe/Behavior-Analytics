"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function OutagePolicyError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="The outage alert policy" digest={error.digest} onRetry={reset} />;
}
