"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function CoverageError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Detection Coverage"
      what="Detection coverage"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
