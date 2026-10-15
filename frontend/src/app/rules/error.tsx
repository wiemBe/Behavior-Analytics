"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function RulesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Rule health" what="Rules" digest={error.digest} onRetry={reset} />;
}
