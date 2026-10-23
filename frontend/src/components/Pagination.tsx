import Link from "next/link";

// Offset pagination control.
//
// A server component: paging is a navigation. `Link` still renders a real href,
// so each page stays shareable and bookmarkable.

function href(
  basePath: string,
  offset: number,
  params: Record<string, string | undefined>,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) qs.set(k, v);
  }
  if (offset > 0) qs.set("offset", String(offset));
  const s = qs.toString();
  return s ? `${basePath}?${s}` : basePath;
}

export function Pagination({
  total,
  limit,
  offset,
  basePath,
  params = {},
}: {
  total: number;
  limit: number;
  offset: number;
  basePath: string;
  params?: Record<string, string | undefined>;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <nav className="pagination" aria-label="Pagination">
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <span className="pagination-links">
        {hasPrev ? (
          <Link href={href(basePath, Math.max(0, offset - limit), params)}>Previous</Link>
        ) : (
          <span className="muted">Previous</span>
        )}
        {hasNext ? (
          <Link href={href(basePath, offset + limit, params)}>Next</Link>
        ) : (
          <span className="muted">Next</span>
        )}
      </span>
    </nav>
  );
}
