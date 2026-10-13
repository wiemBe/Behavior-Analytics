// Filters for the fleet source list: a plain GET form, like the other lists.
// The URL is the shareable description of the view, and submitting drops
// `offset` by not carrying it. Every control has a real label, visually hidden
// so the row stays compact.

import Link from "next/link";

import {
  ALL_CLASSES,
  BASIS_OPTIONS,
  basisLabel,
  classLabel,
  isFleetFiltered,
  reviewLabel,
  type FleetSourceQuery,
} from "@/lib/fleet";

export function FleetSourceFilters({ query }: { query: FleetSourceQuery }) {
  return (
    <form className="filters" method="get" action="/fleet/sources">
      <label className="sr-only" htmlFor="fleet-class">
        Class
      </label>
      <select id="fleet-class" name="class" defaultValue={query.class}>
        <option value="">Any class</option>
        {ALL_CLASSES.map((c) => (
          <option key={c} value={c}>
            {classLabel(c)}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="fleet-review">
        Review status
      </label>
      <select id="fleet-review" name="review_status" defaultValue={query.review_status}>
        <option value="">Any review status</option>
        <option value="NEW">{reviewLabel("NEW")}</option>
        <option value="REVIEWED">{reviewLabel("REVIEWED")}</option>
      </select>

      <label className="sr-only" htmlFor="fleet-conflict">
        Conflicts
      </label>
      <select id="fleet-conflict" name="has_conflict" defaultValue={query.has_conflict}>
        <option value="">Conflict or not</option>
        <option value="true">Conflicting class only</option>
        <option value="false">No conflict</option>
      </select>

      <label className="sr-only" htmlFor="fleet-basis">
        Classification basis
      </label>
      <select id="fleet-basis" name="basis" defaultValue={query.basis}>
        <option value="">Any basis</option>
        {BASIS_OPTIONS.map((b) => (
          <option key={b} value={b}>
            {basisLabel(b)}
          </option>
        ))}
      </select>

      <button type="submit">Filter</button>
      {isFleetFiltered(query) && <Link href="/fleet/sources">Clear</Link>}
    </form>
  );
}
