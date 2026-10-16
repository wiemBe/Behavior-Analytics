"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <BoundaryError title="New custom rule" what="The rule editor" digest={error.digest} onRetry={reset} />;
}
