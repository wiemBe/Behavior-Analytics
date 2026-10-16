import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  return (
    <>
      <p className="muted">
        <Link href="/offenses">← Offenses</Link>
      </p>
      <BoundaryLoading title="Offense" what="this offense" />
    </>
  );
}
