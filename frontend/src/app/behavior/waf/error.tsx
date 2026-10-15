"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function WafBehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="WAF behavior" what="WAF behavior" digest={error.digest} onRetry={reset} />;
}
