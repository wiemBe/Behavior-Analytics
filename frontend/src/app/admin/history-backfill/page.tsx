// What has been read from QRadar's past, and what can still be. The page reads the status from
// the browser: the decision block changes it in place.

import type { Metadata } from "next";

import { HistoryBackfillPage } from "@/components/history/HistoryBackfillPage";
import { PageHeader } from "@/components/ui/PageHeader";

import "@/components/ui/controls.css";

export const metadata: Metadata = {
  title: "History backfill",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="History backfill"
        description="QRadar's past, read once so baselines can learn at once; the window is the history backfill setting. Old data is used for learning only, never judged. Times are UTC."
      />
      <HistoryBackfillPage />
    </>
  );
}
