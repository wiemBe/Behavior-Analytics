"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function IpsSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="IPS sources" what="IPS sources" digest={error.digest} onRetry={reset} />;
}
