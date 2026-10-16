"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function ClassificationRulesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Classification rules"
      what="Classification rules"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
