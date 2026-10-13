"use client";

// Choosing which log source to configure.
//
// Lists the log sources that have no WAF profile, those QRadar classes as WAF
// first (the server orders them). Choosing one opens its editor in create mode;
// nothing is created here.

import { useEffect, useId, useState } from "react";

import { actionErrorMessage, api, type WafCandidateOut } from "@/lib/api";

import "@/components/ui/controls.css";

type State =
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "ready"; items: WafCandidateOut[] };

const LIMIT = 50;

export function AddSourcePicker({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [q, setQ] = useState("");
  const [state, setState] = useState<State>({ kind: "loading" });
  const searchId = useId();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setState({ kind: "loading" });
    const timer = setTimeout(() => {
      api
        .wafCandidates({ q: q.trim() || undefined, limit: LIMIT })
        .then((items) => {
          if (!cancelled) setState({ kind: "ready", items });
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setState({
              kind: "error",
              text: `The log sources could not be listed. ${actionErrorMessage(err)}`,
            });
          }
        });
    }, q === "" ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, q]);

  return (
    <div className="fm-add">
      <button
        type="button"
        className="action"
        aria-expanded={open}
        aria-controls="fm-picker"
        onClick={() => setOpen((o) => !o)}
      >
        Add WAF source
      </button>
      {open && (
        <div id="fm-picker" className="fm-picker panel" role="region" aria-label="Choose a log source">
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
                ? `No log source without a WAF profile has a name containing “${q.trim()}”.`
                : "Every known log source already has a WAF profile."}
            </p>
          )}
          {state.kind === "ready" && state.items.length > 0 && (
            <>
              <ul className="fm-candidates" aria-label="Log sources without a WAF profile">
                {state.items.map((c) => (
                  <li key={c.log_source_id}>
                    <a href={`/admin/waf-sources/${c.log_source_id}?instance=${encodeURIComponent(c.instance_id)}`}>{c.name}</a>
                    <span className="muted">
                      QRadar id {c.qradar_id}
                      {c.type_name ? ` · ${c.type_name}` : ""}
                    </span>
                    {c.source_class === "WAF" && <span className="pill info">Classed as WAF</span>}
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
        </div>
      )}
    </div>
  );
}
