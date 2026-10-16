"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function FleetError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Log-source fleet"
      what="The log-source fleet"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
