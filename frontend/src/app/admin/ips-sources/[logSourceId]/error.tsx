"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IpsSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="IPS source" what="This IPS source" digest={error.digest} onRetry={reset} />;
}
