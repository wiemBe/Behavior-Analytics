import { api, type OffenseDetail, type OffenseHistoryPoint } from "@/lib/api";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatAge, magnitudeTone, offenseStatusTone } from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";


import "../offenses.css";
import "./offense-detail.css";

/** A count that was not measured is a dash, never a zero. */
function count(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("en-US");
}

/** A 0–10 score. Null is an em dash, with no unit attached to a missing measurement. */
function scoreOfTen(value: number | null | undefined): string {
  return value == null ? "—" : `${value} of 10`;
}

function List({ label, values }: { label: string; values: (string | number)[] }) {
  return (
    <div className="detail-block">
      <h4>
        {label} <span className="muted">({values.length})</span>
      </h4>
      {values.length === 0 ? (
        <p className="muted">None recorded.</p>
      ) : (
        <ul className="chips">
          {values.map((v) => (
            <li key={String(v)} className="pill">
              {String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default async function OffenseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const offenseId = Number(id);

  if (!Number.isFinite(offenseId)) {
    return (
      <>
        <PageHeader title="Offense" />
        <div className="notice" role="alert">
          <p>Not a valid offense id.</p>
          <p>Offense ids are numbers. Open the offense from the list.</p>
        </div>
        <p>
          <Link href="/offenses">Back to offenses</Link>
        </p>
      </>
    );
  }

  let offense: OffenseDetail | null = null;
  let history: OffenseHistoryPoint[] | null = [];
  let missing = false;
  let error = false;

  try {
    offense = await api.offense(offenseId);
    // A missing history is not a missing offense — degrade rather than 404.
    history = await api.offenseHistory(offenseId).catch(() => null);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error) {
    return (
      <>
        <PageHeader title={`Offense ${offenseId}`} />
        <div className="notice" role={missing ? "status" : "alert"}>
          <p>
            {missing
              ? "This offense has not been collected. It may be outside the collection window."
              : "Could not load this offense. Try again shortly."}
          </p>
        </div>
        <p>
          <Link href="/offenses">Back to offenses</Link>
        </p>
      </>
    );
  }

  const o = offense!;

  return (
    <>
      <p className="muted">
        <Link href="/offenses">← Offenses</Link>
      </p>
      {/* Text only. QRadar-sourced, sanitized server-side, escaped by React. */}
      <PageHeader
        title={`Offense ${o.qradar_offense_id}`}
        description={o.description?.trim() || "No description."}
      />

      <h2>Scores</h2>
      <dl className="dimension-meta" aria-label="Offense scores">
        <div>
          <dt>Magnitude</dt>
          <dd>
            <span className={`pill ${magnitudeTone(o.magnitude)}`}>{scoreOfTen(o.magnitude)}</span>
            {o.magnitude != null && (
              <span
                className={`mag-bar ${magnitudeTone(o.magnitude)} mag-w-${Math.min(10, Math.max(0, Math.round(o.magnitude)))}`}
                aria-hidden="true"
              />
            )}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            <span className={`pill ${offenseStatusTone(o.status)}`}>{o.status}</span>
          </dd>
        </div>
        <div>
          <dt>Severity</dt>
          <dd>{scoreOfTen(o.severity)}</dd>
        </div>
        <div>
          <dt>Credibility</dt>
          <dd>{scoreOfTen(o.credibility)}</dd>
        </div>
        <div>
          <dt>Relevance</dt>
          <dd>{scoreOfTen(o.relevance)}</dd>
        </div>
        <div>
          <dt>Age</dt>
          <dd>{formatAge(o.age_seconds)}</dd>
        </div>
      </dl>

      <h3>Attributes</h3>
      <TableScroll label="Offense attributes">
      <table className="kv-table" aria-label="Offense attributes">
        <tbody>
          <tr>
            <th>Assigned to</th>
            <td>{o.assigned_to ?? <span className="muted">unassigned</span>}</td>
          </tr>
          <tr>
            <th>Offense type</th>
            <td>{o.offense_type_name ?? "—"}</td>
          </tr>
          <tr>
            <th>Offense source</th>
            <td>{o.offense_source ?? "—"}</td>
          </tr>
          <tr>
            <th>Source network</th>
            <td>{o.source_network ?? "—"}</td>
          </tr>
          <tr>
            <th>Events / Flows</th>
            <td>
              {count(o.event_count)} / {count(o.flow_count)}
            </td>
          </tr>
          <tr>
            <th>Sources / Destinations</th>
            <td>
              {count(o.source_count)} / {count(o.destination_count)}
            </td>
          </tr>
          <tr>
            <th>Started</th>
            <td>{<UtcTime value={o.start_time} />}</td>
          </tr>
          <tr>
            <th>Last updated</th>
            <td>{<UtcTime value={o.last_updated_time} />}</td>
          </tr>
          <tr>
            <th>Closed</th>
            <td>{<UtcTime value={o.close_time} />}</td>
          </tr>
          <tr>
            <th>Last collected</th>
            <td>{<UtcTime value={o.captured_at} />}</td>
          </tr>
        </tbody>
      </table>
      </TableScroll>

      <h3>Entities</h3>
      <div className="detail-grid">
        <List label="Usernames" values={o.usernames ?? []} />
        <List label="Source addresses" values={o.source_addresses ?? []} />
        <List label="Destination addresses" values={o.local_destination_addresses ?? []} />
        <List label="Categories" values={o.categories ?? []} />
        <List label="Contributing rule IDs" values={o.rule_ids ?? []} />
        <List label="Log source IDs" values={o.log_source_ids ?? []} />
      </div>

      <h3>History</h3>
      {history === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>The history could not be loaded.</strong>
          </p>
          <p>
            Nothing was changed, and this does not mean the offense has no history. Reload the
            page; if it keeps failing, check that the backend is reachable.
          </p>
        </div>
      ) : history.length === 0 ? (
        <div className="notice">
          No history yet. A second snapshot is written only when the offense
          changes, so a stable offense has a single entry.
        </div>
      ) : (
        <TableScroll label="Offense history">
        <table aria-label="Offense history">
          <thead>
            <tr>
              <th>Captured</th>
              <th>Status</th>
              <th className="num">Magnitude</th>
              <th className="num">Severity</th>
              <th className="num">Events</th>
              <th>Assigned</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.captured_at}>
                <td className="nowrap">{<UtcTime value={h.captured_at} />}</td>
                <td>{h.status}</td>
                <td className="num">{h.magnitude ?? "—"}</td>
                <td className="num">{h.severity ?? "—"}</td>
                <td className="num">{count(h.event_count)}</td>
                <td>{h.assigned_to ?? <span className="muted">unassigned</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </>
  );
}
