"use client";

// Runs the stored search. The request body is empty: the backend executes the
// saved version, never the text in the editor. The run is queued: the request
// answers 202 with a QUEUED row, and this control follows the row until it is
// terminal. The read is the existing execution list, every five seconds.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { ApiError, api, type SearchExecution } from "@/lib/api";
import { runRequestFailure } from "@/components/searches/authoring";
import { LatestRunStatus } from "@/components/searches/LatestRunStatus";
import { UtcTime } from "@/components/ui/UtcTime";
import { renderBackendMessage } from "@/i18n/backendMessage";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { refusalSentences } from "@/lib/refusal";

import "./searches.css";

export const RUN_POLL_MS = 5000;
const OPEN = new Set(["PENDING", "QUEUED", "RUNNING"]);
const CONFLICT_CODE = "search.run.conflict.running";
const CAPACITY_TYPE = "CAPACITY";

const isOpen = (execution: SearchExecution | null): boolean =>
  execution !== null && OPEN.has(execution.status);

export function SearchRunControl({
  searchId,
  latest = null,
  consecutiveFailures = null,
  pollMs = RUN_POLL_MS,
}: {
  searchId: string;
  /** The stored latest run. The full readout is rendered once, here. */
  latest?: SearchExecution | null;
  consecutiveFailures?: number | null;
  /** The interval of the progress read; a test shortens it. */
  pollMs?: number;
}) {
  const router = useRouter();
  const t = useTranslations("searches.run");
  const i18n = useBehaviorI18n();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [execution, setExecution] = useState<SearchExecution | null>(null);
  const [since, setSince] = useState<string | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const refreshed = useRef<string | null>(null);

  const shown = execution ?? latest;
  const followedId = isOpen(shown) ? (shown?.id ?? null) : null;

  useEffect(() => {
    if (followedId === null) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const rows = await api.searchExecutions(searchId);
          if (cancelled) return;
          const next = rows.find((row) => row.id === followedId);
          setPollFailed(false);
          if (next) setExecution(next);
        } catch {
          // Keep following: the last known state stays on screen and says the read failed.
          if (!cancelled) setPollFailed(true);
        }
      })();
    }, pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [followedId, searchId, pollMs]);

  // The stored history and the failure counter refresh once per run, when it ends.
  useEffect(() => {
    if (execution && !isOpen(execution) && refreshed.current !== execution.id) {
      refreshed.current = execution.id;
      router.refresh();
    }
  }, [execution, router]);

  async function run() {
    if (sending || followedId !== null) return;
    setSending(true);
    setError(null);
    setSince(null);
    setPollFailed(false);
    setExecution(null);
    try {
      const result = await api.runSearch(searchId);
      setExecution(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const [sentence] = refusalSentences(err);
        const stamp = sentence?.code === CONFLICT_CODE ? sentence.params?.since : null;
        setSince(typeof stamp === "string" ? stamp : null);
        setError(typeof stamp === "string" ? null : runRequestFailure(err, i18n));
        // Follow the run that holds the slot.
        try {
          const rows = await api.searchExecutions(searchId);
          const open = rows.find((row) => row.trigger === "MANUAL" && isOpen(row));
          if (open) setExecution(open);
        } catch {
          /* the sentence stays; the next visit shows the run */
        }
      } else {
        setError(runRequestFailure(err, i18n));
      }
    } finally {
      setSending(false);
    }
  }

  const open = isOpen(shown);
  const label = sending
    ? t("running")
    : shown?.status === "RUNNING"
      ? t("running")
      : open
        ? t("queuedButton")
        : t("runNow");
  // A run started on this page has no refreshed failure counter yet.
  const failures = execution ? null : consecutiveFailures;
  const capacity =
    shown && shown.status === "FAILED" && shown.error_type === CAPACITY_TYPE && shown.error_message
      ? renderBackendMessage(
          {
            text: shown.error_message,
            code: shown.error_message_code,
            params: shown.error_message_params,
          },
          i18n.locale,
          i18n.t,
        )
      : null;

  return (
    <div className="search-run">
      <button
        type="button"
        className="action"
        onClick={() => void run()}
        disabled={sending || open}
        aria-busy={sending || open}
      >
        {label}
      </button>
      {open && shown && (
        <div className="banner warn latest-run search-run-note" role="status">
          <p>
            {shown.status === "RUNNING"
              ? shown.started_at
                ? t.rich("runningSince", { time: () => <UtcTime value={shown.started_at} /> })
                : t("runningNow")
              : shown.created_at
                ? t.rich("queuedAt", { time: () => <UtcTime value={shown.created_at} /> })
                : t("queued")}
          </p>
          {pollFailed && <p>{t("pollFailed")}</p>}
        </div>
      )}
      {!open && shown && !capacity && (
        <LatestRunStatus execution={shown} consecutiveFailures={failures} />
      )}
      {!open && capacity && (
        <div className="banner warn latest-run search-run-note" role="status">
          <p>
            {capacity}. {t("capacityAction")}
          </p>
        </div>
      )}
      {since && !open && (
        <div className="banner warn latest-run search-run-note" role="status">
          <p>{t.rich("alreadyRunning", { time: () => <UtcTime value={since} /> })}</p>
        </div>
      )}
      {error && (
        <span role="alert" className="pill crit">
          {error}
        </span>
      )}
    </div>
  );
}
