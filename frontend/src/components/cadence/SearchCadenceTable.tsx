"use client";

// The cadence table: one row per search family.
//
// Each row changes its own cadence with a PUT and reports the outcome on the
// row. Permissions are never judged here: the API answers, and a 403 reverts
// the select and says which permission is missing.

import { useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type SearchCadenceItem } from "@/lib/api";
import {
  cadenceLabel,
  changeFailure,
  familyInfo,
  formatBucket,
  outcomeInfo,
  worstCaseDelay,
} from "@/lib/searchCadence";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

function Outcome({ value }: { value: string }) {
  const info = outcomeInfo(value);
  if (info === null) {
    // An outcome this build does not know is shown as recorded, not guessed at.
    return (
      <span className="cell-sub">
        <code>{value}</code>
      </span>
    );
  }
  return (
    <span className="cell-sub">
      <span className={`pill pill-quiet ${info.tone === "neutral" ? "" : info.tone}`}>
        {info.label}
      </span>
    </span>
  );
}

function CadenceRow({ initial }: { initial: SearchCadenceItem }) {
  const [item, setItem] = useState(initial);
  const [pending, setPending] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });

  const info = familyInfo(item.family);
  const delay = worstCaseDelay(item.cadence_seconds, item.bucket_seconds, item.settle_seconds);
  const lastRun = item.last_run;

  async function change(value: string) {
    const next = Number(value);
    // Each change is an audited write; a second one must wait for the first.
    if (pending !== null || next === item.cadence_seconds) return;
    setPending(next);
    setOutcome({ kind: "pending" });
    try {
      const updated = await api.setSearchCadence(item.family, next);
      setItem(updated);
      const by = updated.updated_by ? `by ${updated.updated_by} ` : "";
      setOutcome({
        kind: "ok",
        text: (
          <>
            Saved. Changed {by}at <UtcTime value={updated.updated_at} />.
          </>
        ),
      });
    } catch (err) {
      setOutcome(
        changeFailure(
          err,
          "Your role cannot change search cadence (permission admin:search_cadence).",
        ),
      );
    } finally {
      setPending(null);
    }
  }

  const hasStatus = outcome.kind !== "idle";

  return (
    <>
      <tr className={hasStatus ? "has-status" : undefined}>
        <td className="cell-family">
          {info.name}
          {info.scope && <span className="cell-sub">{info.scope}</span>}
        </td>
        <td className="cadence-cell">
          <select
            className="select-control"
            aria-label={`Cadence for ${info.name}`}
            value={pending ?? item.cadence_seconds}
            disabled={pending !== null}
            aria-busy={pending !== null}
            onChange={(e) => void change(e.target.value)}
          >
            {item.allowed_cadences_seconds.map((s) => (
              <option key={s} value={s}>
                {cadenceLabel(s)}
              </option>
            ))}
          </select>
        </td>
        <td className="num">
          {item.bucket_seconds == null ? (
            <span className="muted" title="This family has no buckets">
              {formatBucket(null)}
            </span>
          ) : (
            formatBucket(item.bucket_seconds)
          )}
        </td>
        <td className="num">
          <span className="nowrap">{delay.text}</span>
        </td>
        <td className="num">
          {lastRun ? (
            <>
              <UtcTime value={lastRun.at} variant="compact" className="nowrap" />
              {lastRun.outcome && <Outcome value={lastRun.outcome} />}
            </>
          ) : (
            <span className="muted">Not run since upgrade</span>
          )}
        </td>
        <td className="num">
          <UtcTime value={item.next_dispatch_at} variant="compact" className="nowrap" />
        </td>
        <td className="num">
          {lastRun === null ? (
            <span className="muted">No run yet</span>
          ) : lastRun.searches_sent === null ? (
            <span
              className="muted"
              title="This family does not count its searches yet"
            >
              not counted
            </span>
          ) : (
            lastRun.searches_sent
          )}
        </td>
      </tr>
      {hasStatus && (
        <tr className="status-row">
          <td colSpan={7}>
            <ChangeStatus outcome={outcome} />
          </td>
        </tr>
      )}
    </>
  );
}

export function SearchCadenceTable({ items }: { items: SearchCadenceItem[] }) {
  return (
    <>
        <TableScroll label="Search cadence by family">
          <table className="table-dense">
            <caption className="sr-only">
              Search families with their cadence, bucket width, worst-case delay, last run,
              next run and searches sent in the last run. Times are UTC.
            </caption>
            <thead>
              <tr>
                <th scope="col">Family</th>
                <th scope="col">Cadence</th>
                <th scope="col" className="num">
                  Bucket
                  <span className="cell-sub">— = no buckets</span>
                </th>
                <th scope="col" className="num">
                  Worst-case detection delay
                  <span className="cell-sub">cadence + bucket + settle</span>
                </th>
                <th scope="col" className="num">
                  Last run (UTC)
                </th>
                <th scope="col" className="num">
                  Next run (UTC)
                </th>
                <th scope="col" className="num">
                  Searches in last run
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <CadenceRow key={item.family} initial={item} />
              ))}
            </tbody>
          </table>
        </TableScroll>

        <ul className="table-notes" aria-label="How to read the table">
          <li>
            Cadence sets when a family searches QRadar. The bucket width it stores does not
            change.
          </li>
          <li>
            Anomaly explanations have no buckets, so their delay bounds how late evidence
            arrives, not how late a detection does.
          </li>
          <li>
            “Not run since upgrade” means the family has not run since the cadence tracking was
            introduced. “Not counted” means the family does not count its searches yet. Neither
            is zero.
          </li>
        </ul>
    </>
  );
}
