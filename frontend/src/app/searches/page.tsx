import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type ScheduledSearch } from "@/lib/api";

export default async function SearchCatalogPage() {
  let searches: ScheduledSearch[] = [];
  let error = false;
  try {
    searches = await api.searches();
  } catch {
    error = true;
  }

  return (
    <>
      <PageHeader
        title="Search Catalog"
        description="Stored, validated AQL searches. Only persisted definitions are scheduled or executed — the frontend never runs ad-hoc AQL."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>Backend unreachable.</p>
          <p>The search catalog could not be loaded. Check that the stack is running, then reload the page.</p>
        </div>
      ) : searches.length === 0 ? (
        <div className="notice">No scheduled searches defined yet.</div>
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
                    <a href={`/searches/${s.id}`}>{s.name}</a>
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
