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
        description="Settings that control collection, source classification and outage alerting."
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
        <li>
          <Link href="/admin/outage-policy">Outage alerts</Link>
          <span className="cell-sub">Which silent sources raise an alert, and which are only recorded</span>
        </li>
        <li>
          <Link href="/admin/waf-sources">WAF sources</Link>
          <span className="cell-sub">Which QRadar properties each WAF source is read from</span>
        </li>
        <li>
          <Link href="/admin/ips-sources">IPS sources</Link>
          <span className="cell-sub">Which QRadar properties each IPS source is read from</span>
        </li>
      </ul>
    </>
  );
}
