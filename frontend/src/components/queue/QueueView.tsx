"use client";

// The queue: what is new or changed since the last visit, and what waits.
//
// D-59: the stored marker moves every time the page is opened. This component keeps that honest:
//   - it loads the queue WITHOUT `since`, so the server compares with the stored marker, and it
//     remembers that comparison instant as the visit's baseline;
//   - only then, if no source failed and the view is not narrowed or compared with a chosen time,
//     it posts `{as_of, expected, all_sources_ok: true}` once;
//   - every later load of this visit (a filter, a page) asks for `since=<baseline>`, so the New
//     and Updated labels the analyst saw stay put instead of vanishing the moment the marker moved.
// The marker is never moved while a source failed to load: that source's rows were not seen.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { Pagination } from "@/components/Pagination";
import { utcTimeText } from "@/components/ui/UtcTime";
import {
  ApiError,
  queueApi,
  rateLimitedText,
  type QueueCompareKind,
  type QueuePage,
} from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";
import { formatCount, type FormatLocale } from "@/lib/format";
import {
  PAGE_SIZE,
  QUEUE_CAP,
  compareSince,
  isFiltered,
  markerKept,
  queueApiQuery,
  queueHref,
  type QueueQuery,
} from "@/lib/queue";

import { MarkerLine, type MarkerNote, type MarkerShown } from "./MarkerLine";
import { QueueFilters } from "./QueueFilters";
import { QueueTable } from "./QueueTable";
import { SourceStrip } from "./SourceStrip";

import "@/components/ui/controls.css";
import "./queue.css";

interface Visit {
  /** The comparison instant of this visit, from the first load that compared with the stored marker. */
  baseline: string | null;
  kind: QueueCompareKind;
  posting: boolean;
  posted: boolean;
}

type Phase =
  | { status: "loading"; previous: Ready | null }
  | { status: "error"; error: unknown }
  | ({ status: "ready" } & Ready);

interface Ready {
  page: QueuePage;
  shown: MarkerShown;
}

const FRESH: Visit = { baseline: null, kind: "STORED", posting: false, posted: false };

export function QueueView({ query }: { query: QueueQuery }) {
  const t = useTranslations("queue");
  const say = asTextTranslator(useTranslations());
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  const visit = useRef<Visit>({ ...FRESH });
  const [phase, setPhase] = useState<Phase>({ status: "loading", previous: null });
  const [note, setNote] = useState<MarkerNote | null>(null);
  const [nonce, setNonce] = useState(0);
  const view = queueHref(query);

  useEffect(() => {
    let cancelled = false;
    const v = visit.current;
    const override = query.compare ? compareSince(query.compare, Date.now()) : undefined;
    const since = override ?? (v.posted ? (v.baseline ?? undefined) : undefined);
    setPhase((cur) => ({
      status: "loading",
      previous: cur.status === "ready" ? { page: cur.page, shown: cur.shown } : cur.status === "loading" ? cur.previous : null,
    }));

    queueApi
      .list(queueApiQuery(query, since))
      .then((page) => {
        if (cancelled) return;
        if (!override && !v.posted) {
          v.baseline = page.marker.compare_from;
          v.kind = page.marker.kind;
        }
        const shown: MarkerShown =
          !override && v.posted && v.baseline
            ? { kind: v.kind, from: v.baseline }
            : { kind: page.marker.kind, from: page.marker.compare_from };
        setPhase({ status: "ready", page, shown });

        const kept = markerKept(page, query);
        if (kept) {
          if (v.posted) return;
          if (kept.reason === "override") setNote({ kind: "kept-override" });
          else if (kept.reason === "narrowed") setNote({ kind: "kept-narrowed" });
          else
            setNote({
              kind: "kept-failed",
              marker: page.marker.marked_at,
              sources: kept.failed.map((s) => t(`sources.kind.${s.kind}`)),
            });
          return;
        }
        if (v.posted || v.posting) return;
        v.posting = true;
        queueApi
          .seen({ as_of: page.as_of, expected: page.marker.marked_at, all_sources_ok: true })
          .then((out) => {
            v.posted = true;
            v.posting = false;
            setNote({ kind: "advanced", at: out.marked_at });
          })
          .catch((err: unknown) => {
            v.posting = false;
            const detail = err instanceof ApiError ? err.detail : undefined;
            const code = (detail as { code?: unknown } | undefined)?.code;
            const marker = (detail as { marker?: { marked_at?: unknown } } | undefined)?.marker?.marked_at;
            if (err instanceof ApiError && err.status === 409 && code === "queue.marker.changed") {
              setNote({ kind: "changed", marker: typeof marker === "string" ? marker : null });
            } else if (err instanceof ApiError && err.status === 409 && code === "queue.marker.source_failed") {
              setNote({ kind: "refused" });
            } else {
              setNote({ kind: "post-failed" });
            }
          });
      })
      .catch((error: unknown) => {
        if (!cancelled) setPhase({ status: "error", error });
      });

    return () => {
      cancelled = true;
    };
    // The view's address is the whole query; `nonce` is the Reload button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, nonce]);

  function reload() {
    visit.current = { ...FRESH };
    setNote(null);
    setNonce((n) => n + 1);
  }

  const ready: Ready | null =
    phase.status === "ready" ? phase : phase.status === "loading" ? phase.previous : null;
  const loading = phase.status === "loading";

  if (phase.status === "error") {
    const err = phase.error;
    const reason =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("loadFailed.forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("loadFailed.expired")
            : err instanceof ApiError && err.status === 422
              ? t("loadFailed.rejected", { reason: err.message })
              : err instanceof ApiError
                ? t("loadFailed.server")
                : t("loadFailed.network");
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("loadFailed.title")}</strong>
        </p>
        <p>{reason}</p>
        <p>
          <button type="button" className="action secondary" onClick={reload}>
            {t("reload")}
          </button>
        </p>
      </div>
    );
  }

  if (!ready) {
    return (
      <p className="notice" role="status" aria-live="polite">
        {t("loading")}
      </p>
    );
  }

  const { page, shown } = ready;
  const total = page.total;
  const capped = total > QUEUE_CAP;
  const reachable = Math.min(total, QUEUE_CAP);
  const from = page.items.length === 0 ? 0 : page.offset + 1;
  const to = page.offset + page.items.length;
  const unread = page.sources.filter(
    (s) => s.state === "FAILED" || s.state === "NOT_COLLECTED" || s.state === "FORBIDDEN",
  );
  const scopeName = t(`empty.scopeNames.${query.scope}`);

  const params: Record<string, string | string[] | undefined> = {};
  const href = queueHref(query, { offset: 0 });
  for (const [k, value] of new URL(href, "http://x").searchParams.entries()) {
    const have = params[k];
    params[k] = have === undefined ? value : [...(Array.isArray(have) ? have : [have]), value];
  }

  return (
    <div className="queue" aria-busy={loading}>
      {query.ignored.length > 0 && (
        <p className="banner warn queue-ignored" role="status">
          {query.ignored.map((i) => say(`queue.marker.ignored.${i}`)).join(" ")}
        </p>
      )}
      <MarkerLine shown={shown} query={query} note={note} onReload={reload} />
      <SourceStrip sources={page.sources} asOf={page.as_of} totalExact={page.total_exact} />
      <QueueFilters
        query={query}
        scopeTotal={page.total_exact ? formatCount(total, locale) : t("sources.atLeastShort", { count: formatCount(total, locale) })}
      />

      {page.items.length === 0 ? (
        <div className="notice" role="status">
          {unread.length > 0 ? (
            t("empty.unread", { sources: unread.map((s) => t(`sources.kind.${s.kind}`)).join(", ") })
          ) : query.newOnly && !(query.kinds.length || query.families.length || query.severities.length) ? (
            <>
              {query.scope === "all"
                ? t("empty.newOnlyAll", { time: utcTimeText(shown.from) })
                : t("empty.newOnly", { scope: scopeName, time: utcTimeText(shown.from) })}{" "}
              <Link href={queueHref(query, { newOnly: false, offset: 0 })}>{t("empty.showAll")}</Link>
            </>
          ) : isFiltered(query) ? (
            t("empty.filtered")
          ) : query.scope === "all" ? (
            t("empty.all", { time: utcTimeText(page.as_of) })
          ) : (
            t("empty.scope", { scope: scopeName, time: utcTimeText(page.as_of) })
          )}
        </div>
      ) : (
        <>
          {/* The pager below states the range itself; this line covers what it cannot: a total
              that is a lower bound, and a total past the cap the pager does not page into. */}
          {(!page.total_exact || capped) && (
            <p className="subtitle queue-range">
              {t(page.total_exact ? "showing" : "showingAtLeast", {
                from: formatCount(from, locale),
                to: formatCount(to, locale),
                total: formatCount(total, locale),
              })}
            </p>
          )}
          <QueueTable items={page.items} />
          {capped && (
            <p className="banner warn queue-capped" role="status">
              {t("capped")}
            </p>
          )}
          <Pagination
            total={reachable}
            limit={PAGE_SIZE}
            offset={page.offset}
            basePath="/queue"
            params={params}
            rangeText={
              page.total_exact
                ? undefined
                : t("pageRangeAtLeast", {
                    from: formatCount(from, locale),
                    to: formatCount(to, locale),
                    total: formatCount(reachable, locale),
                  })
            }
          />
        </>
      )}
    </div>
  );
}
