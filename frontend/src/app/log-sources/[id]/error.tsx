"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function LogSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Log Source Detail"
      what="This log source"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
