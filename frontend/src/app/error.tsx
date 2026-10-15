"use client";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function DashboardError({
  error,
  reset,
}: {
  error?: Error & { digest?: string };
  reset?: () => void;
}) {
  return (
    <BoundaryError
      title="SOC Overview"
      what="Dashboard could not be opened. The page"
      digest={error?.digest}
      onRetry={reset ?? (() => window.location.reload())}
    />
  );
}
