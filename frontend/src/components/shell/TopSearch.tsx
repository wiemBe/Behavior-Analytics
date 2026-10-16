"use client";

// The top bar's search over lists the application already serves.
//
//   Sources           GET /fleet/sources?q=&limit=5
//   Rules             GET /rules?search=&limit=5
//   Offenses          GET /offenses?search=&limit=5
//   Active anomalies  GET /anomalies?active_only=true&limit=200, read once per
//                     session and filtered here (the API has no text search over
//                     anomalies, so the group says "first 200")
//
// These are reads of the application database; no QRadar search is issued, and
// there are no actions, only links.
//
// A group the role may not read (403) is left out of the results and named once
// in the footer; it is never drawn as "no results". A group whose read failed
// says so in place, apart from a group that was searched and found nothing.
//
// The combobox pattern: the input is the combobox, the arrow keys move
// `aria-activedescendant` through the links, Enter follows the active one and
// Escape closes. Ctrl+K / Cmd+K focuses it.

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { ipsSubjectText, isIpsRow, rowTypeLabel } from "@/components/ips/ipsLabels";
import {
  ApiError,
  shellApi,
  type AnomalySummary,
  type Page,
} from "@/lib/api";

export const DEBOUNCE_MS = 250;
const PER_GROUP = 5;
const MIN_CHARS = 2;

type GroupKey = "sources" | "rules" | "offenses" | "anomalies";

interface Hit {
  id: string;
  href: string;
  title: string;
  sub: string | null;
}

type GroupResult =
  | { key: GroupKey; status: "ok"; hits: Hit[] }
  | { key: GroupKey; status: "forbidden" }
  | { key: GroupKey; status: "error" };

const LABEL: Record<GroupKey, string> = {
  sources: "Sources",
  rules: "Rules",
  offenses: "Offenses",
  anomalies: "Active anomalies (first 200)",
};
// The short name used where the group is a noun in a sentence.
const NAME: Record<GroupKey, string> = {
  sources: "Sources",
  rules: "Rules",
  offenses: "Offenses",
  anomalies: "Active anomalies",
};
const ORDER: GroupKey[] = ["sources", "rules", "offenses", "anomalies"];

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

async function settle(
  key: GroupKey,
  read: () => Promise<Hit[]>,
): Promise<GroupResult> {
  try {
    return { key, status: "ok", hits: (await read()).slice(0, PER_GROUP) };
  } catch (e) {
    if (isAbort(e)) throw e;
    if (e instanceof ApiError && e.status === 403) return { key, status: "forbidden" };
    return { key, status: "error" };
  }
}

export function TopSearch() {
  const router = useRouter();
  const uid = useId();
  const listId = `${uid}-list`;
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const anomaliesRef = useRef<Promise<Page<AnomalySummary>> | null>(null);
  const lifeRef = useRef<AbortController | null>(null);

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState<GroupResult[] | null>(null);
  const [active, setActive] = useState(-1);

  // One signal for the component's lifetime, for the shared anomaly read.
  // Created in the effect (not during render) so StrictMode's second mount gets
  // a live one.
  useEffect(() => {
    lifeRef.current = new AbortController();
    return () => lifeRef.current?.abort();
  }, []);

  const trimmed = query.trim();

  // Debounced search; the previous request is aborted on every change.
  useEffect(() => {
    if (trimmed.length < MIN_CHARS) {
      setGroups(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    // The previous query's results are not shown against this one.
    setGroups(null);
    const timer = setTimeout(async () => {
      const { signal } = controller;
      const needle = trimmed.toLowerCase();
      const anomalies = () => {
        // Cached for the session; a failed read is not cached.
        if (!anomaliesRef.current) {
          anomaliesRef.current = shellApi
            .activeAnomalies(lifeRef.current?.signal)
            .catch((e) => {
              anomaliesRef.current = null;
              throw e;
            });
        }
        return anomaliesRef.current;
      };
      try {
        const results = await Promise.all([
          settle("sources", async () =>
            (await shellApi.searchSources(trimmed, signal)).items.map((s) => ({
              id: `source-${s.id}`,
              href: `/behavior/sources/${s.id}`,
              title: s.name,
              sub: s.type_name,
            })),
          ),
          settle("rules", async () =>
            (await shellApi.searchRules(trimmed, signal)).items.map((r) => ({
              id: `rule-${r.id}`,
              href: `/rules/${r.id}`,
              title: r.name,
              sub: `Rule ${r.qradar_id}`,
            })),
          ),
          settle("offenses", async () =>
            (await shellApi.searchOffenses(trimmed, signal)).items.map((o) => ({
              id: `offense-${o.qradar_offense_id}`,
              href: `/offenses/${o.qradar_offense_id}`,
              title: o.description ?? `Offense ${o.qradar_offense_id}`,
              sub: `#${o.qradar_offense_id} · ${o.status}`,
            })),
          ),
          settle("anomalies", async () => {
            const page = await anomalies();
            if (signal.aborted) throw new DOMException("aborted", "AbortError");
            return page.items
              .filter((a) =>
                // An IPS row is also found by its subject (the entity), which its source name
                // does not contain.
                `${a.log_source_name ?? ""} ${isIpsRow(a) ? ipsSubjectText(a) : ""}`
                  .toLowerCase()
                  .includes(needle),
              )
              .map((a) =>
                isIpsRow(a)
                  ? {
                      id: `anomaly-${a.id}`,
                      href: `/anomalies/${a.id}`,
                      title: ipsSubjectText(a),
                      sub: `${rowTypeLabel(a)} · ${a.log_source_name ?? a.log_source_id} · ${a.state}`,
                    }
                  : {
                      id: `anomaly-${a.id}`,
                      href: `/anomalies/${a.id}`,
                      title: a.log_source_name ?? a.log_source_id,
                      sub: `${a.anomaly_type} · ${a.state}`,
                    },
              );
          }),
        ]);
        if (signal.aborted) return;
        setGroups(results);
        setActive(-1);
        setLoading(false);
      } catch (e) {
        if (!isAbort(e)) {
          setGroups(ORDER.map((key) => ({ key, status: "error" as const })));
          setLoading(false);
        }
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed]);

  // Ctrl+K / Cmd+K focuses the field from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // A click outside closes the panel.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const flat = useMemo(
    () =>
      (groups ?? []).flatMap((g) => (g.status === "ok" ? g.hits : [])),
    [groups],
  );

  const go = useCallback(
    (href: string) => {
      setOpen(false);
      setQuery("");
      router.push(href);
    },
    [router],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      if (flat.length) setActive((i) => (i + 1) % flat.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (flat.length) setActive((i) => (i <= 0 ? flat.length - 1 : i - 1));
    } else if (e.key === "Enter" && open && active >= 0 && flat[active]) {
      e.preventDefault();
      go(flat[active].href);
    }
  };

  const shown = open && trimmed.length > 0;
  const forbidden = (groups ?? []).filter((g) => g.status === "forbidden");
  const searched = (groups ?? []).filter((g) => g.status === "ok");
  const nothingFound =
    groups !== null && !loading && searched.every((g) => g.status === "ok" && g.hits.length === 0);
  const optionId = (h: Hit) => `${uid}-${h.id}`;

  let status: string | null = null;
  if (trimmed.length < MIN_CHARS) status = `Type at least ${MIN_CHARS} characters to search.`;
  else if (loading) status = "Searching…";
  else if (groups && searched.length === 0 && forbidden.length === groups.length)
    status = "Nothing was searched: your role cannot read any of these lists.";
  else if (groups && nothingFound && groups.every((g) => g.status !== "error"))
    status = `No sources, rules, offenses or active anomalies match “${trimmed}”.`;

  return (
    <div className="topsearch" role="search" ref={rootRef}>
      <input
        ref={inputRef}
        type="text"
        className="topsearch-input"
        role="combobox"
        aria-label="Search sources, rules, offenses and anomalies"
        aria-expanded={shown}
        aria-controls={shown ? listId : undefined}
        aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 && flat[active] ? optionId(flat[active]) : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder="Search sources, rules, anomalies"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <kbd className="topsearch-hint" aria-hidden="true">
        Ctrl K
      </kbd>
      {shown && (
        <div className="topsearch-panel">
          {status && (
            <p className="topsearch-status" role="status">
              {status}
            </p>
          )}
          <div id={listId} role="listbox" aria-label="Search results">
            {(groups ?? [])
              .filter((g) => g.status !== "forbidden")
              .map((g) => (
                <div key={g.key} role="group" aria-labelledby={`${uid}-g-${g.key}`}>
                  <div className="topsearch-group" id={`${uid}-g-${g.key}`}>
                    {LABEL[g.key]}
                  </div>
                  {g.status === "error" && (
                    <p className="topsearch-note">
                      {NAME[g.key]} could not be searched: the request failed. Try again.
                    </p>
                  )}
                  {g.status === "ok" && g.hits.length === 0 && !nothingFound && (
                    <p className="topsearch-note">No match.</p>
                  )}
                  {g.status === "ok" &&
                    g.hits.map((h) => (
                      <a
                        key={h.id}
                        id={optionId(h)}
                        href={h.href}
                        role="option"
                        aria-selected={flat[active]?.id === h.id}
                        className={`topsearch-option plain${flat[active]?.id === h.id ? " is-active" : ""}`}
                        onClick={(e) => {
                          // A modified click keeps the browser's own behaviour
                          // (new tab, new window).
                          if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
                          e.preventDefault();
                          go(h.href);
                        }}
                      >
                        <span className="topsearch-title">{h.title}</span>
                        {h.sub && <span className="topsearch-sub">{h.sub}</span>}
                      </a>
                    ))}
                </div>
              ))}
          </div>
          {forbidden.length > 0 && (
            <p className="topsearch-footer">
              {forbidden.map((g) => NAME[g.key]).join(", ")} not searched: your role cannot read
              them
            </p>
          )}
        </div>
      )}
    </div>
  );
}
