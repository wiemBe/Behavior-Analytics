// The endpoint inventory of one WAF source.
//
// Answers "which endpoints are tracked, and how adequate is each baseline". The
// state column is the worst detector state; `null` means no detector has
// evaluated the endpoint, which is said in words and never drawn as NORMAL.

import { TableScroll } from "@/components/ui/TableScroll";
import type { WafEndpointInventory } from "@/lib/api";
import { formatCount, stateTone } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";

import { endpointLabel } from "./wafLabels";

import "@/components/ui/controls.css";

function Utc({ value }: { value: string }) {
  return (
    <time className="nowrap" dateTime={value} title={formatDateTime(value)}>
      {formatDateTimeCompact(value)}
    </time>
  );
}

function BaselineCell({ e }: { e: WafEndpointInventory }) {
  const { cells_total: total, reliable_cells: reliable } = e.baseline;
  return (
    <>
      {total === 0 ? (
        <span>No cells</span>
      ) : (
        <>
          {formatCount(reliable)} / {formatCount(total)}
        </>
      )}
      <span className="cell-sub">
        {total === 0
          ? "No baseline built"
          : reliable === 0
            ? "None reliable yet"
            : "reliable cells"}
      </span>
      {e.baseline_rebuilt_at ? (
        <span className="cell-sub">
          Rebuilt <Utc value={e.baseline_rebuilt_at} />
        </span>
      ) : (
        <span className="cell-sub">Never rebuilt</span>
      )}
    </>
  );
}

export function WafEndpointsTable({ endpoints }: { endpoints: WafEndpointInventory[] }) {
  return (
    <TableScroll label="Tracked endpoints">
      <table className="table-dense">
        <caption className="sr-only">
          Endpoints of this WAF source with baseline adequacy, when each was last seen, whether it
          is retired, and its worst detector state. An endpoint no detector has evaluated says so.
          Times are UTC.
        </caption>
        <thead>
          <tr>
            <th scope="col">Endpoint</th>
            <th scope="col" className="num">
              Baseline cells
              <span className="cell-sub">reliable / total</span>
            </th>
            <th scope="col" className="num">
              Last seen (UTC)
            </th>
            <th scope="col">Tracking</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {endpoints.map((e) => (
            <tr key={e.id}>
              <td className="cell-name">
                <span className="waf-endpoint">{endpointLabel(e)}</span>
                {e.virtual_host && <span className="cell-sub">Virtual host {e.virtual_host}</span>}
                <span className="cell-sub">Normalizer v{e.normalizer_version}</span>
              </td>
              <td className="num">
                <BaselineCell e={e} />
              </td>
              <td className="num">
                <Utc value={e.last_seen_at} />
                <span className="cell-sub">
                  First <Utc value={e.first_seen_at} />
                </span>
              </td>
              <td>
                {e.retired_at ? (
                  <>
                    <span className="pill pill-quiet warn">Retired</span>
                    <span className="cell-sub">
                      <Utc value={e.retired_at} />
                    </span>
                  </>
                ) : e.is_active ? (
                  "Active"
                ) : (
                  // Inactive without a retirement time: not tracked, reason not recorded.
                  <>
                    <span className="pill pill-quiet">Inactive</span>
                    <span className="cell-sub">Retirement time not recorded</span>
                  </>
                )}
              </td>
              <td>
                {e.state ? (
                  <span className={`pill pill-quiet ${stateTone(e.state)}`}>{e.state}</span>
                ) : (
                  <>
                    <span className="muted">Not evaluated</span>
                    <span className="cell-sub">No detector has judged this endpoint</span>
                  </>
                )}
                {e.active_anomaly_count > 0 && (
                  <span className="cell-sub">
                    {formatCount(e.active_anomaly_count)} active{" "}
                    {e.active_anomaly_count === 1 ? "anomaly" : "anomalies"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
