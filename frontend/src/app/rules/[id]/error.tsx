"use client";

import Link from "next/link";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function RulesIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <>
      <p className="muted">
        <Link href="/rules">← Rule Health</Link>
      </p>
      <BoundaryError title="Rule" what="This rule" digest={error.digest} onRetry={reset} />
    </>
  );
}
