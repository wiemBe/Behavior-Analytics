"use client";

// Choosing the source to override.
//
// The search runs on the server (`q` on the fleet source list, a substring of
// the name or the QRadar type), debounced, so it covers the whole inventory. A
// page that holds fewer rows than matched says how many matched in total.

import { useEffect, useId, useState } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { actionErrorMessage, fleetApi, type FleetSource } from "@/lib/api";
import { classLabel } from "@/lib/fleet";

import "@/components/ui/controls.css";

const SHOWN = 25;
const DEBOUNCE_MS = 250;

type State =
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "ready"; items: FleetSource[]; total: number };

export function SourcePicker({
  overridden,
  onChoose,
  onEdit,
  onClose,
}: {
  /** Ids that already have a global SOURCE row. */
  overridden: Set<string>;
  onChoose: (source: FleetSource) => void;
  /** Opens the editor of an existing override. */
  onEdit: (sourceId: string) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [q, setQ] = useState("");
  const searchId = useId();

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    const timer = setTimeout(
      () => {
        fleetApi
          .sources({ q: q.trim() || undefined, limit: SHOWN })
          .then((page) => {
            if (!cancelled) setState({ kind: "ready", items: page.items, total: page.total });
          })
          .catch((err: unknown) => {
            if (!cancelled) {
              setState({
                kind: "error",
                text: `The log sources could not be listed. ${actionErrorMessage(err)}`,
              });
            }
          });
      },
      q.trim() === "" ? 0 : DEBOUNCE_MS,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [q]);

  return (
    <div className="fm-picker panel" role="region" aria-label="Choose a log source">
      <div className="fm-picker-search">
        <label htmlFor={searchId}>Search by name or QRadar type</label>
        <input
          id={searchId}
          type="search"
          className="field fm-input"
          value={q}
          maxLength={100}
          autoComplete="off"
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {state.kind === "loading" && (
        <p className="fm-note" role="status">
          Searching log sources…
        </p>
      )}
      {state.kind === "error" && (
        <p className="fm-note is-crit" role="alert">
          {state.text}
        </p>
      )}
      {state.kind === "ready" && state.items.length === 0 && (
        <p className="fm-empty" role="status">
          {q.trim()
            ? `No log source has a name or type containing “${q.trim()}”.`
            : "The inventory lists no log source."}
        </p>
      )}
      {state.kind === "ready" && state.items.length > 0 && (
        <TableScroll label="Log sources">
          <table className="fleet-table outage-pick">
            <caption className="sr-only">
              Log sources matching the search, with their class and QRadar type.
            </caption>
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Class</th>
                <th scope="col">QRadar type</th>
                <th scope="col" className="cell-end">
                  Override
                </th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((s) => (
                <tr key={s.id}>
                  <th scope="row" className="cell-source">
                    {s.name}
                    {!s.enabled && <span className="cell-sub">Disabled</span>}
                  </th>
                  <td>{classLabel(s.source_class)}</td>
                  <td>{s.type_name ?? <span className="muted">—</span>}</td>
                  <td className="cell-end">
                    {overridden.has(s.id) ? (
                      <a
                        href={`#override-${s.id}`}
                        aria-label={`Has override: edit ${s.name}`}
                        onClick={(e) => {
                          e.preventDefault();
                          onEdit(s.id);
                        }}
                      >
                        Has override
                      </a>
                    ) : (
                      <button
                        type="button"
                        className="action secondary"
                        aria-label={`Override ${s.name}`}
                        onClick={() => onChoose(s)}
                      >
                        Override
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      {state.kind === "ready" && state.total > state.items.length && (
        <p className="fm-note">
          {state.total} log sources match; showing the first {state.items.length}. Narrow the search
          to see others.
        </p>
      )}
      <div className="actions">
        <button type="button" className="action secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
