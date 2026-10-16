"use client";

// Runs the stored search. The request body is empty: the backend executes the
// saved version, never the text in the editor.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, type SearchExecution } from "@/lib/api";
import { runRequestFailure } from "@/components/searches/authoring";
import { LatestRunStatus } from "@/components/searches/LatestRunStatus";

import "./searches.css";

export function SearchRunControl({
  searchId,
  latest = null,
  consecutiveFailures = null,
}: {
  searchId: string;
  /** The stored latest run. The full readout is rendered once, here. */
  latest?: SearchExecution | null;
  consecutiveFailures?: number | null;
}) {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [execution, setExecution] = useState<SearchExecution | null>(null);

  async function run() {
    if (running) return;
    setRunning(true);
    setError(null);
    setExecution(null);
    try {
      const result = await api.runSearch(searchId);
      setExecution(result);
      router.refresh();
    } catch (err) {
      setError(runRequestFailure(err));
    } finally {
      setRunning(false);
    }
  }

  const shown = execution ?? latest;
  // A run started on this page has no refreshed failure counter yet.
  const failures = execution ? null : consecutiveFailures;

  return (
    <div className="search-run">
      <button
        type="button"
        className="action"
        onClick={() => void run()}
        disabled={running}
        aria-busy={running}
      >
        {running ? "Running…" : "Run now"}
      </button>
      {shown && <LatestRunStatus execution={shown} consecutiveFailures={failures} />}
      {error && (
        <span role="alert" className="pill crit">
          {error}
        </span>
      )}
    </div>
  );
}
