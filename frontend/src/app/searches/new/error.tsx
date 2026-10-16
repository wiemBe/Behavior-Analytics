"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function NewSearchError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError title="New search" what="The search editor" digest={error.digest} onRetry={reset} />
  );
}
