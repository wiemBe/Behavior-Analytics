import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/PageHeader";

import { AdminIndex } from "./AdminIndex";

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
      <AdminIndex />
    </>
  );
}
