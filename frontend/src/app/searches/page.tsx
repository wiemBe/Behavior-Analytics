import Link from "next/link";

import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import { searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type ScheduledSearch } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";

export default async function SearchCatalogPage() {
  let searches: ScheduledSearch[] = [];
  let failed: string | null = null;
  let access: SearchAccess = "unknown";
  try {
    const [rows, caller] = await Promise.all([
      api.searches(),
      api.caller().then(searchAccessFrom).catch(() => "unknown" as const),
    ]);
    searches = rows;
    access = caller;
  } catch (err) {
    failed = loadErrorMessage(err, "searches", "read:searches");
    try {
      access = searchAccessFrom(await api.caller());
    } catch {
      access = "unknown";
    }
  }

  return (
    <>
      <PageHeader
        title="Search Catalog"
        description="Stored, validated AQL searches. Only persisted definitions are scheduled or executed — the frontend never runs ad-hoc AQL."
        actions={
          access === "writer" ? (
            <Link className="action secondary" href="/searches/new">
              New search
            </Link>
          ) : null
        }
      />

      {failed ? (
        <div className="notice" role="alert">
          <p>{failed}</p>
        </div>
      ) : searches.length === 0 ? (
        <div className="notice">
          <p>No scheduled searches defined yet.</p>
          {access === "writer" ? (
            <p>Use New search to store a disabled definition, then run the stored query before enabling it.</p>
          ) : (
            <p>
              Creating a search needs <code>search:write</code>. An admin or the{" "}
              <code>detection_engineer</code> role can grant it.
            </p>
          )}
        </div>
      ) : (
        <TableScroll label="Scheduled searches">
          <table className="table-dense" aria-label="Scheduled searches">
            <thead>
              <tr>
                <th>Name</th>
                <th>Category</th>
                <th>MITRE</th>
                <th>Schedule</th>
                <th>Severity</th>
                <th>Ver</th>
                <th>Enabled</th>
              </tr>
            </thead>
            <tbody>
              {searches.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/searches/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.category ?? "—"}</td>
                  <td>{s.mitre_techniques.join(", ") || "—"}</td>
                  <td>
                    <ScheduleLabel cron={s.schedule_cron} />
                  </td>
                  <td><span className={`pill ${sevTone(s.severity)}`}>{s.severity}</span></td>
                  <td>v{s.query_version}</td>
                  <td>{s.enabled ? "yes" : <span className="pill warn">no</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
