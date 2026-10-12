"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function BehaviorError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="Behavioral analytics" digest={error.digest} onRetry={reset} />;
}
