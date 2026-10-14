// Which silent sources page someone, and which are only recorded.

import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  outagePolicyApi,
  type OutageFleetSummary,
  type OutagePolicyRow,
} from "@/lib/api";

import { OutagePolicyManager } from "./OutagePolicyManager";
import { readError } from "./outage";

export const metadata: Metadata = {
  title: "Outage alerts",
};

export default async function OutagePolicyPage() {
  let rows: OutagePolicyRow[] = [];
  let summary: OutageFleetSummary | null = null;
  let summaryNote: string | null = null;
  let failure: { forbidden: boolean; text: string } | null = null;

  const [policy, fleet] = await Promise.allSettled([
    outagePolicyApi.list(),
    outagePolicyApi.summary("24h"),
  ]);
  if (policy.status === "fulfilled") {
    rows = policy.value;
  } else {
    failure = readError(policy.reason);
  }
  if (fleet.status === "fulfilled") {
    summary = fleet.value;
  } else {
    summaryNote =
      fleet.reason instanceof ApiError && fleet.reason.status === 403
        ? "The source counts are not shown: your role cannot read the fleet summary (permission read:fleet). The policy below is complete."
        : "The source counts are not shown: the fleet summary could not be loaded. The policy below is complete; grace is given in buckets only.";
  }

  return (
    <>
      <PageHeader
        title="Outage alerts"
        description="Which silent sources page someone, and which are only recorded. Times are UTC."
      />

      {failure ? (
        <div className="notice" role="alert">
          <p>
            <strong>
              {failure.forbidden
                ? "The outage alert policy cannot be shown to your role."
                : "The outage alert policy could not be shown."}
            </strong>
          </p>
          <p>{failure.text}</p>
        </div>
      ) : (
        <>
          <div className="banner outage-note">
            <p>
              <strong>Record only</strong> still records the outage: the silence is detected, stays
              on the source and on <code>/behavior</code>, and follows its normal lifecycle. It
              only stops that anomaly from raising an alert. An alert that is already open
              resolves normally. Only <code>NO_EVENTS</code> is affected; volume drops and spikes
              still alert.
            </p>
          </div>
          <OutagePolicyManager
            initialRows={rows}
            initialSummary={summary}
            summaryNote={summaryNote}
          />
        </>
      )}
    </>
  );
}
