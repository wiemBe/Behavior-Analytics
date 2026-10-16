import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  return (
    <>
      <p className="muted">
        <Link href="/rules">← Rule Health</Link>
      </p>
      <BoundaryLoading title="Rule" what="this rule" />
    </>
  );
}
