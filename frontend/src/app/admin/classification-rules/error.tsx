"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function ClassificationRulesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="Classification rules" digest={error.digest} onRetry={reset} />;
}
