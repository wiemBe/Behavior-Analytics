"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function LoginError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <BoundaryError title="Sign in" what="Sign in" digest={error.digest} onRetry={reset} />;
}
