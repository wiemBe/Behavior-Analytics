"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function OffensesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Offenses" what="Offenses" digest={error.digest} onRetry={reset} />;
}
