"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function RuleStatisticsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="Rule statistics" digest={error.digest} onRetry={reset} />;
}
