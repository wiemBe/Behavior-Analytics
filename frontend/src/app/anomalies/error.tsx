"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function AnomaliesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Anomalies" what="Anomalies" digest={error.digest} onRetry={reset} />;
}
