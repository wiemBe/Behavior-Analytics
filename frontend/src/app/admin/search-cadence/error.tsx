"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function SearchCadenceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Search cadence" what="Search cadence" digest={error.digest} onRetry={reset} />;
}
