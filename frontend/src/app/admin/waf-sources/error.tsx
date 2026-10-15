"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="WAF sources" what="WAF sources" digest={error.digest} onRetry={reset} />;
}
