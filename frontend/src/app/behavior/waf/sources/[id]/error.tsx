"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="WAF source endpoints"
      what="WAF source endpoints"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
