"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function RuleStatisticsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Rule statistics"
      what="Rule statistics"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
