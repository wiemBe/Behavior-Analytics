"use client";

// The senders queue: which unconfigured devices feed the catch-all sources.
//
// Filters live in component state and each change asks the API again; the page
// has already fetched the default view (New senders, every source) on the
// server. A row owns its sender: Review and Dismiss replace the row with the
// API's answer, and a failure leaves it exactly as it was, with the reason.
// The row stays in the list after a change so it does not jump from under the
// pointer; "Reload list" applies the filter again.

import { useTranslations } from "next-intl";
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
import { useFleetText, type FleetText } from "@/lib/fleetText";

import { SenderId } from "./SenderId";
import {
  STATUS_FILTERS,
  WRITE_PERMISSION,
  deniedNote,
  emptyKind,
  formatCount,
  formatEvents24h,
  reviewedBy,
  senderFailure,
  statusFilterLabel,
  statusLabel,
  statusTone,
  type StatusFilter,
} from "./catchall";
import { unitGap } from "@/i18n/measures";

import "@/components/ui/controls.css";

export const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 50;

type View =
  | { kind: "ready"; items: CatchallSender[]; total: number }
  | { kind: "loading"; items: CatchallSender[]; total: number }
  | { kind: "error"; text: string; forbidden: boolean };

function readFailure(err: unknown, text: FleetText): { text: string; forbidden: boolean } {
  if (err instanceof ApiError && err.status === 403) {
    return { forbidden: true, text: text.say("fleet.errors.forbidden") };
  }
  if (err instanceof ApiError && err.status === 401) {
    return { forbidden: false, text: text.say("errors.sessionExpired") };
  }
  return { forbidden: false, text: text.say("catchall.queue.unreachable") };
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
  const t = useTranslations("catchall.queue");
  const text = useFleetText();
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
        text: kind === "review" ? t("markedReviewed") : t("dismissed"),
      });
      onChanged();
    } catch (err) {
      const failure = senderFailure(err, text);
      if (failure.kind === "forbidden") onDenied();
      setOutcome(failure);
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  }

  const by = reviewedBy(sender);
  const disabled = pending !== null || denied;
  const reason = denied ? deniedNote(text) : undefined;

  return (
    <tr>
      <th scope="row" className="cell-sender">
        <SenderId value={sender.identifier} />
        {!sender.active && <span className="cell-sub">{t("notSeen")}</span>}
      </th>
      <td className="cell-source">{sender.catchall_log_source_name}</td>
      <td>
        <span className="seen">
          <span className="seen-label">{t("first")}</span> <Time value={sender.first_seen_at} bare />
        </span>
        <span className="seen">
          <span className="seen-label">{t("last")}</span> <Time value={sender.last_seen_at} bare />
        </span>
      </td>
      <td className="num" title={text.say("catchall.events24hNote")}>
        {sender.events_24h == null ? (
          <span className="muted">{formatEvents24h(null, text)}</span>
        ) : (
          <>
            {formatEvents24h(sender.events_24h, text)}
            <span className="unit">
              {unitGap(text.locale)}
              {t("eventsUnit")}
            </span>
          </>
        )}
      </td>
      <td>
        <span className={`pill ${statusTone(sender.review_status)}`}>
          {statusLabel(sender.review_status, text)}
        </span>
        {sender.review_status !== "NEW" && (
          <span className="cell-sub">
            {by && sender.reviewed_at ? (
              t.rich("by", {
                name: by,
                time: () => <Time value={sender.reviewed_at as string} />,
              })
            ) : (
              t("reviewerNotRecorded")
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
              aria-label={t("reviewFor", { name: sender.identifier })}
              aria-describedby={denied ? denialId : undefined}
              title={reason}
              disabled={disabled}
              onClick={() => void act("review")}
            >
              {t("review")}
            </button>
          )}
          {sender.review_status !== "DISMISSED" && (
            <button
              type="button"
              className="action secondary"
              aria-label={t("dismissFor", { name: sender.identifier })}
              aria-describedby={denied ? denialId : undefined}
              title={reason}
              disabled={disabled}
              onClick={() => void act("dismiss")}
            >
              {t("dismiss")}
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
  const t = useTranslations("catchall.queue");
  const text = useFleetText();
  if (kind === "never-collected") {
    return (
      <div className="notice">
        <p>
          <strong>{t("neverHeading")}</strong>
        </p>
        <p>{t("neverBody")}</p>
      </div>
    );
  }
  if (kind === "filtered") {
    return (
      <div className="notice">
        <p>
          <strong>{t("filteredHeading")}</strong>
        </p>
        <p>{t("filteredBody")}</p>
      </div>
    );
  }
  if (kind === "clear") {
    return (
      <div className="notice">
        <p>
          <strong>{t("clearHeading")}</strong>
        </p>
        <p>
          {t("clearBody")}
          {latest ? (
            <>
              {" "}
              {t.rich("clearLatest", { time: () => <UtcTime value={latest} /> })}
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
          {status === "ALL"
            ? t("noneAll")
            : t("noneStatus", {
                status: statusFilterLabel(status, text).toLocaleLowerCase(
                  text.locale === "tr" ? "tr-TR" : "en-US",
                ),
              })}
        </strong>
      </p>
      <p>{t("noneBody")}</p>
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
  const t = useTranslations("catchall.queue");
  const common = useTranslations("common");
  const text = useFleetText();
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
  // The effect below must not restart when the translator object changes identity.
  const textRef = useRef(text);
  textRef.current = text;
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
        if (!cancelled) setView({ kind: "error", ...readFailure(err, textRef.current) });
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
      setMoreError(t("moreError"));
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
        {t("heading")}
      </h2>

      <div className="filters" role="search" aria-label={t("filterLabel")}>
        <label className="sr-only" htmlFor={`${ids}-status`}>
          {t("reviewStatus")}
        </label>
        <select
          id={`${ids}-status`}
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
        >
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s}>
              {statusFilterLabel(s, text)}
              {s === "NEW" ? ` (${formatCount(newCount, text.locale)})` : ""}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`${ids}-source`}>
          {t("catchallSource")}
        </label>
        <select
          id={`${ids}-source`}
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
        >
          <option value="">{t("allSources")}</option>
          {sources.items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`${ids}-q`}>
          {t("searchLabel")}
        </label>
        <input
          id={`${ids}-q`}
          type="search"
          placeholder={t("searchPlaceholder")}
          value={typed}
          maxLength={255}
          onChange={(e) => setTyped(e.target.value)}
        />
        <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
          {t("reloadList")}
        </button>
      </div>

      {denied && (
        <p id={denialId} className="subtitle denied-note" role="status">
          {t.rich("deniedNote", {
            perm: () => <code className="perm">{WRITE_PERMISSION}</code>,
          })}
        </p>
      )}

      {view.kind === "error" ? (
        <div className="notice" role="alert">
          <p>
            <strong>
              {view.forbidden ? t("errorForbidden") : t("errorFailed")}
            </strong>
          </p>
          <p>{view.text}</p>
          {!view.forbidden && (
            <p>
              <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
                {common("tryAgain")}
              </button>
            </p>
          )}
        </div>
      ) : (
        <div aria-busy={view.kind === "loading"}>
          {view.kind === "loading" && (
            <p className="subtitle" role="status">
              {t("loading")}
            </p>
          )}
          {view.kind === "ready" && rows.length === 0 ? (
            <EmptyQueue kind={emptyKind({ filtered, status, anyMeasured })} status={status} latest={latest} />
          ) : (
            rows.length > 0 && (
              <>
                {view.kind === "ready" && (
                  <p className="subtitle queue-count" role="status">
                    {t("showing", {
                      shown: formatCount(rows.length, text.locale),
                      total: formatCount(total, text.locale),
                    })}
                  </p>
                )}
                <TableScroll label={t("tableLabel")}>
                  <table className="fleet-table catchall-table">
                    <caption className="sr-only">{t("caption")}</caption>
                    <thead>
                      <tr>
                        <th scope="col">{t("thSender")}</th>
                        <th scope="col">{t("thSource")}</th>
                        <th scope="col">{t("thSeen")}</th>
                        <th scope="col" className="num" title={text.say("catchall.events24hNote")}>
                          {t("thEvents")}
                        </th>
                        <th scope="col">{t("thReview")}</th>
                        <th scope="col">{t("thActions")}</th>
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
                    {t.rich("note", {
                      b: (chunks) => <strong>{chunks}</strong>,
                      i: (chunks) => <em>{chunks}</em>,
                    })}
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
                      {loadingMore ? t("loadingMore") : t("loadMore")}
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
