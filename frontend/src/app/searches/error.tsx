"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function SearchesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError
      title="Search Catalog"
      what="Scheduled searches"
      digest={error.digest}
      onRetry={reset}
    />
  );
}
