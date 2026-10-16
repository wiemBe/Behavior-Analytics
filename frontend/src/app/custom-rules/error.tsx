"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <BoundaryError title="Custom rules" what="Custom rules" digest={error.digest} onRetry={reset} />;
}
