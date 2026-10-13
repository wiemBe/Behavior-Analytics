"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function IpsSourceError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="This IPS source" digest={error.digest} onRetry={reset} />;
}
