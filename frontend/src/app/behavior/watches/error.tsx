"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <BoundaryError title="Property watches" what="Property watches" digest={error.digest} onRetry={reset} />;
}
