import {
  api,
  type CapacitySample,
  type RuleDetail,
  type RuleHealthSnapshot,
  ruleStatisticsApi,
} from "@/lib/api";
import Link from "next/link";
import { CapacityHistory } from "@/components/rules/CapacityHistory";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatShare } from "@/lib/format";
import { healthMeaning, healthTone, isUnestablished } from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

import "../rules.css";

export default async function RuleDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let rule: RuleDetail | null = null;
  let history: RuleHealthSnapshot[] | null = [];
  let capacityHistory: CapacitySample[] | null = [];
  let missing = false;
  let error = false;

  try {
    [rule, history, capacityHistory] = await Promise.all([
      api.rule(id),
      api.ruleHealthHistory(id).catch(() => null),
      ruleStatisticsApi.capacityHistory(id).catch(() => null),
    ]);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error || !rule) {
    return (
      <>
        <PageHeader title="Rule" />
        <div className="notice" role={missing ? "status" : "alert"}>
          <p>
            {missing
              ? "This rule has not been collected."
              : "Could not load this rule. Try again shortly."}
          </p>
        </div>
        <p>
          <Link href="/rules">Back to rules</Link>
        </p>
      </>
    );
  }

  const r = rule;
  // The reason stored with the verdict on the card above, not the newest history row: the two
  // can differ, and a reason must belong to the verdict it sits beside.
  const reason = r.health_reason?.trim() || null;
  const buildingBlocks = r.dependencies.filter((d) => d.kind === "BUILDING_BLOCK");
  const telemetry = r.dependencies.filter((d) => d.kind !== "BUILDING_BLOCK");

  return (
    <>
      <p className="muted">
        <Link href="/rules">← Rule Health</Link>
      </p>
      <PageHeader
        title={r.name}
        description={`QRadar rule ${r.qradar_id}${r.is_building_block ? " · building block" : ""}`}
      />

      <div className="cards rule-cards rule-cards-2">
        <div className="card">
          <div className="k">Health</div>
          <div className={`v ${healthTone(r.health_status)}`}>
            {r.health_status?.replace(/_/g, " ") ?? "—"}
          </div>
        </div>
        <div className="card">
          <div className="k">Enabled</div>
          <div className={`v ${r.enabled ? "ok" : "warn"}`}>{r.enabled ? "Yes" : "No"}</div>
        </div>
        <div className="card">
          <div className="k">Offense contributions</div>
          <div className="v">{r.offense_contribution_count}</div>
        </div>
        <div className="card">
          <div className="k">Event contributions</div>
          <div className="v">{r.event_contribution_count}</div>
        </div>
      </div>

      <h3>Health evidence</h3>
      {isUnestablished(r.health_status) ? (
        <div className="notice">
          <strong>{healthMeaning(r.health_status)}</strong>
          <p className="rule-health-reason">
            {reason ??
              "No complete rule-metric observation covers this rule. Missing data is reported as unestablished rather than as a detection gap."}
          </p>
        </div>
      ) : (
        <p className="rule-health-reason">{reason ?? healthMeaning(r.health_status)}</p>
      )}

      <div className="detail-grid">
        <div className="detail-block">
          <h4>Building blocks ({buildingBlocks.length})</h4>
          {buildingBlocks.length === 0 ? (
            <p className="muted">No building-block dependencies were exposed by QRadar.</p>
          ) : (
            <ul>
              {buildingBlocks.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {d.target_name ?? d.target_ref} · {d.source.toLowerCase()} · {formatShare(d.confidence)}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="detail-block">
          <h4>Telemetry requirements ({telemetry.length})</h4>
          {telemetry.length === 0 ? (
            <p className="muted">No log-source dependencies were exposed by QRadar.</p>
          ) : (
            <ul>
              {telemetry.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {d.target_name ?? d.target_ref} · {d.kind.replace(/_/g, " ")} · {d.source.toLowerCase()}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <h3>Metadata</h3>
      <TableScroll label="Rule metadata">
      <table className="kv-table" aria-label="Rule metadata">
        <tbody>
          <tr>
            <th>Rule type</th>
            <td>{r.rule_type ?? "—"}</td>
          </tr>
          <tr>
            <th>Origin</th>
            <td>{r.origin ?? "—"}</td>
          </tr>
          <tr>
            <th>Owner</th>
            <td>{r.owner ?? "—"}</td>
          </tr>
          <tr>
            <th>Building block</th>
            <td>{r.is_building_block ? "Yes" : "No"}</td>
          </tr>
          <tr>
            <th>Last fired</th>
            <td>
              {r.last_fired_at ? (
                <UtcTime value={r.last_fired_at} />
              ) : (
                <span className="muted">not observed</span>
              )}
            </td>
          </tr>
          <tr>
            <th>Health evaluated</th>
            <td>{<UtcTime value={r.health_evaluated_at} />}</td>
          </tr>
          <tr>
            <th>MITRE techniques</th>
            <td>
              {r.mitre_techniques.length === 0 ? (
                <span className="muted">none mapped</span>
              ) : (
                <ul className="chips">
                  {r.mitre_techniques.map((t) => (
                    <li key={t} className="pill">
                      {t}
                    </li>
                  ))}
                </ul>
              )}
            </td>
          </tr>
        </tbody>
      </table>
      </TableScroll>

      <CapacityHistory samples={capacityHistory} />

      <h3>Health history</h3>
      {history === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>The health history could not be loaded.</strong>
          </p>
          <p>
            Nothing was changed, and this does not mean no evaluation was stored. Reload the
            page; if it keeps failing, check that the backend is reachable.
          </p>
        </div>
      ) : history.length === 0 ? (
        <div className="notice">No rule-health evaluation has been stored yet.</div>
      ) : (
        <TableScroll label="Rule health history">
        <table className="rules-history table-dense" aria-label="Rule health history">
          <thead>
            <tr>
              <th>Evaluated</th>
              <th>Status</th>
              <th className="num">Confidence</th>
              <th className="num">Triggers</th>
              <th className="num">Offenses</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.evaluated_at}>
                <td className="nowrap">{<UtcTime value={h.evaluated_at} />}</td>
                <td><span className={`pill ${healthTone(h.status)}`}>{h.status.replace(/_/g, " ")}</span></td>
                <td className="num">{formatShare(h.confidence)}</td>
                <td className="num">{h.trigger_count}</td>
                <td className="num">{h.offense_contribution_count}</td>
                <td className="muted cell-reason">{h.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </>
  );
}
