"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function AlertsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Alerts" what="Alerts" digest={error.digest} onRetry={reset} />;
}
