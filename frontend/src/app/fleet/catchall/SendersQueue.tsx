"use client";

// The senders queue: which unconfigured devices feed the catch-all sources.
//
// Filters live in component state and each change asks the API again; the page
// has already fetched the default view (New senders, every source) on the
// server. A row owns its sender: Review and Dismiss replace the row with the
// API's answer, and a failure leaves it exactly as it was, with the reason.
// The row stays in the list after a change so it does not jump from under the
// pointer; "Reload list" applies the filter again.

import { useEffect, useId, useRef, useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  catchallApi,
  type CatchallReviewStatus,
  type CatchallSender,
  type CatchallSourceList,
  type Page,
} from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import { SenderId } from "./SenderId";
import {
  DENIED_NOTE,
  EVENTS_24H_NOTE,
  STATUS_FILTERS,
  STATUS_LABELS,
  WRITE_PERMISSION,
  emptyKind,
  formatCount,
  formatEvents24h,
  reviewedBy,
  senderFailure,
  statusFilterLabel,
  statusTone,
  type StatusFilter,
} from "./catchall";

import "@/components/ui/controls.css";

export const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 50;

type View =
  | { kind: "ready"; items: CatchallSender[]; total: number }
  | { kind: "loading"; items: CatchallSender[]; total: number }
  | { kind: "error"; text: string; forbidden: boolean };

function readFailure(err: unknown): { text: string; forbidden: boolean } {
  if (err instanceof ApiError && err.status === 403) {
    return {
      forbidden: true,
      text: "Your role cannot read the fleet (permission read:fleet). Nothing was changed.",
    };
  }
  if (err instanceof ApiError && err.status === 401) {
    return { forbidden: false, text: "Your session has expired. Sign in again to continue." };
  }
  return {
    forbidden: false,
    text: "The senders could not be loaded. The backend may be unreachable; check that it is running, then try again. Your filters are kept.",
  };
}

/** `bare` drops the zone from the text where the column header names it; the title keeps it. */
function Time({ value, bare = false }: { value: string; bare?: boolean }) {
  return <UtcTime value={value} variant="compact" bare={bare} className="nowrap" />;
}

function SenderRow({
  initial,
  denied,
  denialId,
  onDenied,
  onChanged,
}: {
  initial: CatchallSender;
  denied: boolean;
  denialId: string;
  onDenied: () => void;
  onChanged: () => void;
}) {
  const [sender, setSender] = useState(initial);
  const [pending, setPending] = useState<"review" | "dismiss" | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  // State updates are asynchronous; a second click in the same tick would read
  // a stale `pending`. The ref closes that window.
  const inFlight = useRef(false);

  async function act(kind: "review" | "dismiss") {
    if (inFlight.current || denied) return;
    inFlight.current = true;
    setPending(kind);
    setOutcome({ kind: "pending" });
    try {
      const updated =
        kind === "review"
          ? await catchallApi.review(sender.id)
          : await catchallApi.dismiss(sender.id);
      setSender(updated);
      setOutcome({
        kind: "ok",
        text:
          kind === "review"
            ? "Marked reviewed. It leaves the New queue when the list is reloaded."
            : "Dismissed. It leaves the New queue when the list is reloaded.",
      });
      onChanged();
    } catch (err) {
      const failure = senderFailure(err);
      if (failure.kind === "forbidden") onDenied();
      setOutcome(failure);
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  }

  const by = reviewedBy(sender);
  const disabled = pending !== null || denied;
  const reason = denied ? DENIED_NOTE : undefined;

  return (
    <tr>
      <th scope="row" className="cell-sender">
        <SenderId value={sender.identifier} />
        {!sender.active && <span className="cell-sub">Not seen recently</span>}
      </th>
      <td className="cell-source">{sender.catchall_log_source_name}</td>
      <td>
        <span className="seen">
          <span className="seen-label">First</span> <Time value={sender.first_seen_at} bare />
        </span>
        <span className="seen">
          <span className="seen-label">Last</span> <Time value={sender.last_seen_at} bare />
        </span>
      </td>
      <td className="num" title={EVENTS_24H_NOTE}>
        {sender.events_24h == null ? (
          <span className="muted">{formatEvents24h(null)}</span>
        ) : (
          <>
            {formatEvents24h(sender.events_24h)}
            <span className="unit"> events</span>
          </>
        )}
      </td>
      <td>
        <span className={`pill ${statusTone(sender.review_status)}`}>
          {STATUS_LABELS[sender.review_status]}
        </span>
        {sender.review_status !== "NEW" && (
          <span className="cell-sub">
            {by && sender.reviewed_at ? (
              <>
                by {by} · <Time value={sender.reviewed_at} />
              </>
            ) : (
              "Reviewer not recorded"
            )}
          </span>
        )}
      </td>
      <td className="cell-actions">
        <div className="actions">
          {sender.review_status !== "REVIEWED" && (
            <button
              type="button"
              className="action secondary"
              aria-label={`Review ${sender.identifier}`}
              aria-describedby={denied ? denialId : undefined}
              title={reason}
              disabled={disabled}
              onClick={() => void act("review")}
            >
              Review
            </button>
          )}
          {sender.review_status !== "DISMISSED" && (
            <button
              type="button"
              className="action secondary"
              aria-label={`Dismiss ${sender.identifier}`}
              aria-describedby={denied ? denialId : undefined}
              title={reason}
              disabled={disabled}
              onClick={() => void act("dismiss")}
            >
              Dismiss
            </button>
          )}
        </div>
        <ChangeStatus outcome={outcome} />
      </td>
    </tr>
  );
}

function EmptyQueue({
  kind,
  status,
  latest,
}: {
  kind: ReturnType<typeof emptyKind>;
  status: StatusFilter;
  latest: string | null;
}) {
  if (kind === "never-collected") {
    return (
      <div className="notice">
        <p>
          <strong>Nothing has been collected yet.</strong>
        </p>
        <p>
          No catch-all bucket has been measured for any source, so an empty list says nothing
          about who is sending. Senders appear after the first catch-all collection runs.
        </p>
      </div>
    );
  }
  if (kind === "filtered") {
    return (
      <div className="notice">
        <p>
          <strong>No sender matches these filters.</strong>
        </p>
        <p>Widen the status, choose all sources or clear the search to see more senders.</p>
      </div>
    );
  }
  if (kind === "clear") {
    return (
      <div className="notice">
        <p>
          <strong>The queue is clear.</strong>
        </p>
        <p>
          No sender is waiting for review.
          {latest ? (
            <>
              {" "}
              The latest measured bucket starts <UtcTime value={latest} />.
            </>
          ) : (
            ""
          )}
        </p>
      </div>
    );
  }
  return (
    <div className="notice">
      <p>
        <strong>
          No sender is {status === "ALL" ? "tracked" : statusFilterLabel(status).toLowerCase()}.
        </strong>
      </p>
      <p>Choose another status to see the other senders.</p>
    </div>
  );
}

export function SendersQueue({
  sources,
  initial,
}: {
  sources: CatchallSourceList;
  initial: Page<CatchallSender>;
}) {
  const [status, setStatus] = useState<StatusFilter>("NEW");
  const [sourceId, setSourceId] = useState("");
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [tick, setTick] = useState(0);
  const [view, setView] = useState<View>({
    kind: "ready",
    items: initial.items,
    total: initial.total,
  });
  const [denied, setDenied] = useState(false);
  const [newCount, setNewCount] = useState(
    sources.items.reduce((sum, s) => sum + s.new_sender_count, 0),
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const firstRun = useRef(true);
  const ids = useId();
  const denialId = `${ids}-denied`;

  // `q` follows what is typed after a pause, so a request is not sent per key.
  useEffect(() => {
    const handle = setTimeout(() => setQ(typed.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [typed]);

  useEffect(() => {
    // The server already fetched the default view.
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    let cancelled = false;
    setView((v) => ({
      kind: "loading",
      items: v.kind === "error" ? [] : v.items,
      total: v.kind === "error" ? 0 : v.total,
    }));
    setMoreError(null);
    catchallApi
      .senders({
        review_status: status === "ALL" ? undefined : (status as CatchallReviewStatus),
        catchall_log_source_id: sourceId || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
      })
      .then((page) => {
        if (!cancelled) setView({ kind: "ready", items: page.items, total: page.total });
      })
      .catch((err: unknown) => {
        if (!cancelled) setView({ kind: "error", ...readFailure(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [status, sourceId, q, tick]);

  function refreshCounts() {
    catchallApi
      .sources()
      .then((list) => setNewCount(list.items.reduce((sum, s) => sum + s.new_sender_count, 0)))
      .catch(() => {
        /* the count is a convenience; the rows are the truth */
      });
  }

  async function loadMore() {
    if (view.kind !== "ready" || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await catchallApi.senders({
        review_status: status === "ALL" ? undefined : (status as CatchallReviewStatus),
        catchall_log_source_id: sourceId || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: view.items.length,
      });
      setView({ kind: "ready", items: [...view.items, ...page.items], total: page.total });
    } catch {
      setMoreError("The next senders could not be loaded. The ones shown are unchanged; try again.");
    } finally {
      setLoadingMore(false);
    }
  }

  const filtered = sourceId !== "" || q !== "";
  const anyMeasured = sources.items.some((s) => s.latest_bucket_start !== null);
  const latest =
    sources.items
      .map((s) => s.latest_bucket_start)
      .filter((v): v is string => v !== null)
      .sort()
      .at(-1) ?? null;
  const rows = view.kind === "error" ? [] : view.items;
  const total = view.kind === "error" ? 0 : view.total;

  return (
    <section aria-labelledby={`${ids}-h`} className="catchall-queue">
      <h2 id={`${ids}-h`} className="section-title">
        Senders queue
      </h2>

      <div className="filters" role="search" aria-label="Filter senders">
        <label className="sr-only" htmlFor={`${ids}-status`}>
          Review status
        </label>
        <select
          id={`${ids}-status`}
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
        >
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s}>
              {statusFilterLabel(s)}
              {s === "NEW" ? ` (${formatCount(newCount)})` : ""}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`${ids}-source`}>
          Catch-all source
        </label>
        <select
          id={`${ids}-source`}
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
        >
          <option value="">All catch-all sources</option>
          {sources.items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`${ids}-q`}>
          Search sender
        </label>
        <input
          id={`${ids}-q`}
          type="search"
          placeholder="Search sender, e.g. fw-edge"
          value={typed}
          maxLength={255}
          onChange={(e) => setTyped(e.target.value)}
        />
        <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
          Reload list
        </button>
      </div>

      {denied && (
        <p id={denialId} className="subtitle denied-note" role="status">
          Review and Dismiss are disabled: they need the permission{" "}
          <code className="perm">{WRITE_PERMISSION}</code>, which your role does not have.
        </p>
      )}

      {view.kind === "error" ? (
        <div className="notice" role="alert">
          <p>
            <strong>
              {view.forbidden ? "The senders cannot be shown." : "The senders could not be loaded."}
            </strong>
          </p>
          <p>{view.text}</p>
          {!view.forbidden && (
            <p>
              <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
                Try again
              </button>
            </p>
          )}
        </div>
      ) : (
        <div aria-busy={view.kind === "loading"}>
          {view.kind === "loading" && (
            <p className="subtitle" role="status">
              Loading senders…
            </p>
          )}
          {view.kind === "ready" && rows.length === 0 ? (
            <EmptyQueue kind={emptyKind({ filtered, status, anyMeasured })} status={status} latest={latest} />
          ) : (
            rows.length > 0 && (
              <>
                {view.kind === "ready" && (
                  <p className="subtitle queue-count" role="status">
                    Showing {formatCount(rows.length)} of {formatCount(total)}{" "}
                    {total === 1 ? "sender" : "senders"}.
                  </p>
                )}
                <TableScroll label="Catch-all senders">
                  <table className="fleet-table catchall-table">
                    <caption className="sr-only">
                      Senders feeding the catch-all sources, New first and then by 24 hour events,
                      busiest first. Senders with unknown events come last.
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Sender</th>
                        <th scope="col">Catch-all source</th>
                        <th scope="col">Seen (UTC)</th>
                        <th scope="col" className="num" title={EVENTS_24H_NOTE}>
                          24 h events
                        </th>
                        <th scope="col">Review</th>
                        <th scope="col">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((s) => (
                        <SenderRow
                          key={`${s.id}:${s.review_status}:${s.reviewed_at ?? ""}`}
                          initial={s}
                          denied={denied}
                          denialId={denialId}
                          onDenied={() => setDenied(true)}
                          onChanged={refreshCounts}
                        />
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
                <ul className="table-notes">
                  <li>
                    <strong>24 h events</strong> is a lower bound, the sum of the stored top-sender
                    lists that name the sender. <em>unknown</em> means no stored list named it; it
                    is not zero.
                  </li>
                </ul>
                {rows.length < total && (
                  <div className="load-more">
                    <button
                      type="button"
                      className="action secondary"
                      disabled={loadingMore || view.kind !== "ready"}
                      onClick={() => void loadMore()}
                    >
                      {loadingMore ? "Loading…" : "Show more senders"}
                    </button>
                    {moreError && (
                      <p className="change-status crit" role="alert">
                        {moreError}
                      </p>
                    )}
                  </div>
                )}
              </>
            )
          )}
        </div>
      )}
    </section>
  );
}
