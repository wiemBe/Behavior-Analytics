"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function SearchCadenceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="Search cadence" digest={error.digest} onRetry={reset} />;
}
