import type { Metadata } from "next";
import Link from "next/link";

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
          <Link href="/admin/waf-sources">WAF sources</Link>
          <span className="cell-sub">Which QRadar properties each WAF source is read from</span>
        </li>
      </ul>
    </>
  );
}
