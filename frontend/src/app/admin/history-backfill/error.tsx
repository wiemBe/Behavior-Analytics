"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function HistoryBackfillError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="History backfill"
      what="The history backfill"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
