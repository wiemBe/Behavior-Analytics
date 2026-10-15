"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function AlertsIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Alert Detail" what="This alert" digest={error.digest} onRetry={reset} />;
}
