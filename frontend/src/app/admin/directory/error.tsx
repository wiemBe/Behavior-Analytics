"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function DirectoryError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Directory" what="Directory" digest={error.digest} onRetry={reset} />;
}
