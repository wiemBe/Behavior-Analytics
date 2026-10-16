"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function PasswordError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <BoundaryError title="Change password" what="Change password" digest={error.digest} onRetry={reset} />
  );
}
