"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function SearchesIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Search detail" what="This search" digest={error.digest} onRetry={reset} />;
}
