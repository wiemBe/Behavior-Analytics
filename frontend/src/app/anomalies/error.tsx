"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function AnomaliesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="Anomalies" digest={error.digest} onRetry={reset} />;
}
