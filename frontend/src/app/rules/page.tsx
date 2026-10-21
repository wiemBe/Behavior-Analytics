import { api, type Page, type RuleHealthCount, type RuleSummary } from "@/lib/api";
import Link from "next/link";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  healthMeaning,
  healthTone,
  isUnestablished,
} from "@/lib/health";
import { axisTimeLabel } from "@/lib/searchRuns";

import "@/components/ui/controls.css";
import "./rules.css";

const PAGE_SIZE = 25;

export default async function RulesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const search = params.search ?? "";
  const health = params.health_status ?? "";

  let page: Page<RuleSummary> | null = null;
  let summary: RuleHealthCount[] = [];
  let error: string | null = null;

  try {
    [page, summary] = await Promise.all([
      api.rules({ limit: PAGE_SIZE, offset, search, health_status: health }),
      api.ruleHealthSummary(),
    ]);
  } catch (err) {
    error = loadErrorMessage(err, "rules", "read:rules");
  }

  if (error) {
    return (
      <>
        <PageHeader
          title="Rule health"
          actions={<Link href="/rules/statistics">Rule statistics</Link>}
        />
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      </>
    );
  }

  const items = page?.items ?? [];
  const unestablished = summary
    .filter((s) => isUnestablished(s.status))
    .reduce((n, s) => n + s.count, 0);

  return (
    <>
      <PageHeader
        title="Rule health"
        description="Analytics rules collected from QRadar, with a health verdict derived from what has actually been observed. Building blocks are collected but are not listed here."
        actions={<Link href="/rules/statistics">Rule statistics</Link>}
      />

      {summary.length > 0 && (
        <div className={`cards rules-cards rules-cards-${Math.ceil(summary.length / 2)}`}>
          {summary.map((s) => (
            <StatCard
              key={s.status}
              label={s.status.replace(/_/g, " ")}
              value={s.count}
              tone={healthTone(s.status) || undefined}
            />
          ))}
        </div>
      )}

      {unestablished > 0 && (
        <div className="notice">
          <strong>{unestablished} rules have no health verdict yet.</strong>{" "}
          QRadar&rsquo;s rule inventory API exposes no last-fired timestamp and no
          rule statistics, so silence cannot be distinguished from never having
          been observed. These are reported as <code>INSUFFICIENT_DATA</code>{" "}
          rather than being counted as detection gaps.
        </div>
      )}

      <form className="filters" method="get" action="/rules">
        <input
          type="search"
          name="search"
          defaultValue={search}
          placeholder="Search rule name…"
          aria-label="Search rules"
        />
        <select
          name="health_status"
          defaultValue={health}
          aria-label="Filter by health status"
        >
          <option value="">All health states</option>
          {summary.map((s) => (
            <option key={s.status} value={s.status}>
              {s.status.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <button type="submit">Filter</button>
        {(search || health) && <Link href="/rules">Clear</Link>}
      </form>

      {items.length === 0 ? (
        <div className="notice">
          {search || health
            ? "No rules match these filters."
            : "No rules collected yet. Run a rule sync to pull them from QRadar."}
        </div>
      ) : (
        <>
          <TableScroll label="Rules">
          <table className="rule-list-table table-dense" aria-label="Rules">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Type</th>
                <th>Enabled</th>
                <th>Health</th>
                <th>Meaning</th>
                <th className="col-time">Last fired (UTC)</th>
                <th className="num">Offenses</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id}>
                  <td className="cell-rule-name">
                    <a href={`/rules/${r.id}`}>{r.name}</a>
                  </td>
                  <td>{r.rule_type ?? "—"}</td>
                  <td>
                    <span className={`pill ${r.enabled ? "ok" : "warn"}`}>
                      {r.enabled ? "enabled" : "disabled"}
                    </span>
                  </td>
                  <td>
                    <span className={`pill ${healthTone(r.health_status)}`}>
                      {r.health_status?.replace(/_/g, " ") ?? "—"}
                    </span>
                  </td>
                  <td className="muted cell-meaning">{healthMeaning(r.health_status)}</td>
                  <td className="nowrap">
                    {r.last_fired_at ? (
                      axisTimeLabel(r.last_fired_at)
                    ) : (
                      <span className="muted">not observed</span>
                    )}
                  </td>
                  <td className="num">{r.offense_contribution_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/rules"
            params={{ search, health_status: health }}
          />
        </>
      )}
    </>
  );
}
