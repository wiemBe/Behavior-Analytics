"use client";

import Link from "next/link";

import { BoundaryError } from "@/components/boundary/BoundaryLoading";

export default function OffensesIdError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <>
      <p className="muted">
        <Link href="/offenses">← Offenses</Link>
      </p>
      <BoundaryError
        title="Offense"
        what="This offense"
        digest={error.digest}
        onRetry={reset}
      />
    </>
  );
}
