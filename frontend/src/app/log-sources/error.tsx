"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function LogSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Log Sources" what="Log sources" digest={error.digest} onRetry={reset} />;
}
