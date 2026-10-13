"use client";

import { ErrorState } from "@/components/ui/RouteState";

export default function IpsSourcesError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState what="IPS sources" digest={error.digest} onRetry={reset} />;
}
