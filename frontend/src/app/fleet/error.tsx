"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function FleetError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="The log-source fleet" digest={error.digest} onRetry={reset} />;
}
