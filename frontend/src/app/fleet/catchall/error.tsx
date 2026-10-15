"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function CatchallError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Catch-all senders"
      what="The catch-all senders page"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
