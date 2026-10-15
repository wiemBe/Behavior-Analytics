// The catch-all senders view.
//
// The question: which unconfigured devices are sending to the catch-all
// sources, and how much? The queue answers it first; the per-source buckets
// beneath show the volume and what was and was not measured.

import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  catchallApi,
  type CatchallSender,
  type CatchallSourceList,
  type Page,
} from "@/lib/api";

import { SendersQueue } from "./SendersQueue";
import { SourceVolume } from "./SourceVolume";

import "./catchall.css";

export const metadata: Metadata = {
  title: "Catch-all senders",
};

// Rendered per request: the queue is live data.
export const dynamic = "force-dynamic";

function failureText(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return "Your role cannot read the fleet (permission read:fleet). Nothing was changed.";
  }
  if (err instanceof ApiError && err.status === 401) {
    return "Your session has expired. Sign in again to continue.";
  }
  return "The catch-all senders could not be loaded. The backend may be unreachable; check that it is running, then reload the page.";
}

export default async function CatchallPage() {
  let sources: CatchallSourceList | null = null;
  let initial: Page<CatchallSender> | null = null;
  let error: string | null = null;
  const now = new Date().toISOString();
  try {
    sources = await catchallApi.sources();
    if (sources.items.length > 0) {
      initial = await catchallApi.senders({ review_status: "NEW", limit: 50 });
    }
  } catch (err) {
    error = failureText(err);
  }

  return (
    <>
      <PageHeader
        title="Catch-all senders"
        description="A sender is the syslog hostname QRadar recorded (logsourceidentifier) for events that landed in a catch-all source. It is often a relay, and it is a candidate unconfigured device, not a verdict. Times are UTC."
      />
      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>The catch-all senders could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources === null || sources.items.length === 0 || initial === null ? (
        <div className="notice">
          <p>
            <strong>No catch-all source is in the inventory.</strong>
          </p>
          <p>
            A log source counts as catch-all when its QRadar device type is one of{" "}
            {sources && sources.catchall_type_ids.length > 0
              ? sources.catchall_type_ids.join(", ")
              : "the configured catch-all types"}
            . Run an inventory sync from the Log Sources page; a matching source appears here once
            it is synced.
          </p>
        </div>
      ) : (
        <>
          <SendersQueue sources={sources} initial={initial} />
          <SourceVolume sources={sources} now={now} />
        </>
      )}
    </>
  );
}
