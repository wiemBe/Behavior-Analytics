"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function BehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Behavioral overview"
      what="Behavioral analytics"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
