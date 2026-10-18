// The Definition tab's generated query text (design §G.13.3): what the collector would send for
// this watch, and what it recorded for the newest bucket. Read-only text in a collapsed
// `CodePanel`; nothing here is an input and nothing is ever sent back (AGENTS.md §2 rule 4).
//
// The text may contain a pinned value (D-22) and is attacker-controlled: `CodePanel` renders it
// as text. A failed read, an unresolvable scope and a watch that has not run each say so; none
// of them is an empty panel.

import { CodePanel } from "@/components/ui/CodePanel";
import type { PwAqlQuery, WatchAql } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

/** The outcome of the AQL read: the text, or the HTTP status that refused it (0: no answer). */
export type AqlRead = { kind: "ok"; aql: WatchAql } | { kind: "error"; status: number };

export const AQL_NOTE =
  "Generated query text. The collector adds the time range and the page limit when it runs.";

const SEARCH_LABELS: Record<PwAqlQuery["search"], string> = {
  GROUPED: "Value counts",
  TOTALS: "Scope total",
  PINNED: "Pinned values",
};

function Queries({ prefix, queries }: { prefix: string; queries: PwAqlQuery[] }) {
  return (
    <div className="pw-query-list">
      {queries.map((q) => (
        <CodePanel
          key={q.search}
          label={`${prefix} · ${SEARCH_LABELS[q.search] ?? q.search}`}
          code={q.text}
          meta="Read-only"
        />
      ))}
    </div>
  );
}

export function WatchAqlSection({ read }: { read: AqlRead | null }) {
  if (read === null || read.kind === "error") {
    const forbidden = read !== null && read.status === 403;
    return (
      <div className="notice" role="alert">
        <p>
          <strong>
            {forbidden
              ? "The generated query text is forbidden for this account."
              : "The generated query text could not be loaded."}
          </strong>
        </p>
        <p>
          {forbidden
            ? "Reading it needs read:property_watches. Ask an admin to grant a role that includes it."
            : "The backend may be unreachable: reload the page to try again. Nothing was changed."}
        </p>
      </div>
    );
  }
  const { aql } = read;
  const last = aql.last_bucket;
  return (
    <div className="stack-6">
      <p className="subtitle">{AQL_NOTE}</p>

      <div>
        <h3 className="pw-subtitle">Current definition</h3>
        {aql.current_unavailable ? (
          <div className="notice" role="status">
            <p>
              <strong>The query text for the current definition could not be generated.</strong>
            </p>
            <p>{aql.current_unavailable}</p>
          </div>
        ) : aql.current.length === 0 ? (
          <p className="notice" role="status">
            No query text was generated for the current definition.
          </p>
        ) : (
          <>
            <p className="subtitle">
              Generated for{" "}
              {aql.window_start && aql.window_end ? (
                <>
                  <time dateTime={aql.window_start}>{formatDateTime(aql.window_start)}</time>
                  {" → "}
                  <time dateTime={aql.window_end}>{formatDateTime(aql.window_end)}</time>
                </>
              ) : (
                "a window that was not recorded"
              )}
              , watch version {aql.watch_version}.
            </p>
            <Queries prefix="Current" queries={aql.current} />
          </>
        )}
      </div>

      <div>
        <h3 className="pw-subtitle">Last collected bucket</h3>
        {last === null ? (
          <p className="notice" role="status">
            No bucket collected yet, so no query text has been recorded.
          </p>
        ) : (
          <>
            <p className="subtitle">
              Bucket <time dateTime={last.bucket_start}>{formatDateTime(last.bucket_start)}</time>,
              collected <time dateTime={last.collected_at}>{formatDateTime(last.collected_at)}</time>,
              watch version {last.watch_version}
              {last.watch_version !== aql.watch_version
                ? `; the watch has since changed to version ${aql.watch_version}`
                : ""}
              .
            </p>
            {last.queries.length === 0 ? (
              <p className="notice" role="status">
                No query text was recorded for that bucket. A search that was not sent leaves none.
              </p>
            ) : (
              <Queries prefix="Last bucket" queries={last.queries} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
