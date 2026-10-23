"use client";

import Link from "next/link";


// Choosing which log source to configure.
//
// Lists the log sources that have no IPS profile, those QRadar classes as IPS
// first (the server orders them). Choosing one opens its editor in create mode;
// nothing is created here. The route needs the QRadar instance, so a page that
// could not name one says so instead of listing nothing.

import { useEffect, useId, useState } from "react";

import { ApiError, actionErrorMessage, ipsApi, type IpsCandidateOut } from "@/lib/api";

import "@/components/ui/controls.css";

type State =
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "ready"; items: IpsCandidateOut[] };

const LIMIT = 50;

export function AddIpsSourcePicker({
  instanceId,
  defaultOpen = false,
}: {
  /** Null: no QRadar instance could be determined. */
  instanceId: string | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [q, setQ] = useState("");
  const [state, setState] = useState<State>({ kind: "loading" });
  const searchId = useId();

  useEffect(() => {
    if (!open || !instanceId) return;
    let cancelled = false;
    setState({ kind: "loading" });
    const timer = setTimeout(
      () => {
        ipsApi
          .candidates({ instance_id: instanceId, q: q.trim() || undefined, limit: LIMIT })
          .then((items) => {
            if (!cancelled) setState({ kind: "ready", items });
          })
          .catch((err: unknown) => {
            if (!cancelled) {
              setState({
                kind: "error",
                text: `The log sources could not be listed. ${
                  err instanceof ApiError && err.status === 403
                    ? "Your role cannot list them (permission admin:ips)."
                    : actionErrorMessage(err)
                }`,
              });
            }
          });
      },
      q === "" ? 0 : 250,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, q, instanceId]);

  return (
    <div className="fm-add">
      <button
        type="button"
        className="action"
        aria-expanded={open}
        aria-controls="fm-picker"
        onClick={() => setOpen((o) => !o)}
      >
        Add IPS source
      </button>
      {open && (
        <div id="fm-picker" className="fm-picker panel" role="region" aria-label="Choose a log source">
          {!instanceId ? (
            <p className="fm-note is-crit" role="alert">
              The log sources cannot be listed: no QRadar instance is known yet, because the
              platform has not collected any log source. Run a log-source sync first.
            </p>
          ) : (
            <>
              <div className="fm-picker-search">
                <label htmlFor={searchId}>Search log sources by name</label>
                <input
                  id={searchId}
                  type="search"
                  className="field fm-input"
                  value={q}
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
                    ? `No log source without an IPS profile has a name containing “${q.trim()}”.`
                    : "Every known log source already has an IPS profile."}
                </p>
              )}
              {state.kind === "ready" && state.items.length > 0 && (
                <>
                  <ul className="fm-candidates" aria-label="Log sources without an IPS profile">
                    {state.items.map((c) => (
                      <li key={c.log_source_id}>
                        <Link href={`/admin/ips-sources/${c.log_source_id}`}>{c.name}</Link>
                        <span className="muted">
                          QRadar id {c.qradar_id}
                          {c.type_name ? ` · ${c.type_name}` : ""}
                        </span>
                        {c.source_class === "IPS" && <span className="pill info">Classed as IPS</span>}
                      </li>
                    ))}
                  </ul>
                  {state.items.length >= LIMIT && (
                    <p className="fm-note">
                      Showing the first {LIMIT}. Narrow the search to see others.
                    </p>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
