"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function UsersError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Users" what="Users" digest={error.digest} onRetry={reset} />;
}
