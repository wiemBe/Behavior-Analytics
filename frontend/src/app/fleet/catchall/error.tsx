"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function CatchallError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="The catch-all senders page" digest={error.digest} onRetry={reset} />;
}
