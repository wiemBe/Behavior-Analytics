import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/PageHeader";

import "@/components/ui/controls.css";

export const metadata: Metadata = {
  title: "Administration",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Administration"
        description="Settings that change how the platform runs. Further sections arrive in Phase 4."
      />
      <ul className="admin-links" aria-label="Administration sections">
        <li>
          <a href="/admin/search-cadence">Search cadence</a>
          <span className="cell-sub">How often each search family queries QRadar</span>
        </li>
        <li>
          <a href="/admin/classification-rules">Classification rules</a>
          <span className="cell-sub">Which rules decide a log source&apos;s class, and in what order</span>
        </li>
      </ul>
    </>
  );
}
