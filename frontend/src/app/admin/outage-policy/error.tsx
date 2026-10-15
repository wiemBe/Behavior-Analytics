"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function OutagePolicyError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Outage alerts"
      what="The outage alert policy"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
