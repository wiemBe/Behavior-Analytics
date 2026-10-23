"use client";

// Connection status: up to three named signals and the worst of them as a word.
//
//   1. Backend and database   GET /health/ready
//   2. QRadar instance        GET /overview (status, version, last health check)
//   3. Collection             GET /admin/search-cadence (last PHASE_A_VOLUME run)
//
// Rules that keep it honest:
//   - the control shows the worst signal, ranked crit > warn > neutral > ok, so
//     a signal that is merely unknown stops the control reading as healthy;
//   - UNKNOWN is neutral, never green;
//   - an instance that was never health-checked says so, and is neutral whatever
//     its stored status: nothing established that status;
//   - a signal the role may not read (403) is omitted, not shown as bad;
//   - "Flow Collectors" and "Event Processors" are not here: no data source.

import { useEffect, useRef, useState, type ReactNode } from "react";

import type { Loaded } from "./TopBar";
import type { HealthReady, SearchCadenceItem, SocOverview } from "@/lib/api";
import { outcomeInfo } from "@/lib/searchCadence";
import { relativeAge } from "@/lib/time";
import { UtcTime } from "@/components/ui/UtcTime";

export type SignalTone = "ok" | "warn" | "crit" | "neutral";

export interface Signal {
  key: string;
  name: string;
  tone: SignalTone;
  value: string;
  detail: ReactNode | null;
}

const RANK: Record<SignalTone, number> = { ok: 0, neutral: 1, warn: 2, crit: 3 };

/** The worst of the signals, or null when none is shown. */
export function worstTone(signals: Signal[]): SignalTone | null {
  if (signals.length === 0) return null;
  return signals.reduce<SignalTone>((w, s) => (RANK[s.tone] > RANK[w] ? s.tone : w), "ok");
}

const WORD: Record<SignalTone, string> = {
  ok: "Connected",
  neutral: "Unknown",
  warn: "Degraded",
  crit: "Problem",
};

const INSTANCE_TONE: Record<string, SignalTone> = {
  HEALTHY: "ok",
  DEGRADED: "warn",
  UNREACHABLE: "crit",
  UNKNOWN: "neutral",
};

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

function ageText(iso: string, now: number | null): string {
  if (now === null) return "";
  const age = relativeAge(iso, now);
  return age === "—" ? "" : `, ${age}`;
}

export function buildSignals(
  health: Loaded<HealthReady>,
  overview: Loaded<SocOverview>,
  cadence: Loaded<SearchCadenceItem[]>,
  now: number | null,
): Signal[] {
  const out: Signal[] = [];

  // 1. The backend itself. A refusal here is still "cannot reach", not omitted:
  //    /health/ready is open, so a 403 means something in front of it is wrong.
  if (health.state === "loading") {
    out.push({ key: "backend", name: "Backend and database", tone: "neutral", value: "Checking", detail: null });
  } else if (health.state === "ok" && health.data.status === "ready" && health.data.database === "ok") {
    out.push({ key: "backend", name: "Backend and database", tone: "ok", value: "OK", detail: null });
  } else {
    out.push({
      key: "backend",
      name: "Backend and database",
      tone: "crit",
      value: "Backend unreachable",
      detail: null,
    });
  }

  // 2. The QRadar instance, as the backend last established it.
  if (overview.state === "loading") {
    out.push({ key: "instance", name: "QRadar instance", tone: "neutral", value: "Checking", detail: null });
  } else if (overview.state === "error") {
    out.push({
      key: "instance",
      name: "QRadar instance",
      tone: "neutral",
      value: "Unavailable",
      detail: "The overview could not be read.",
    });
  } else if (overview.state === "ok") {
    const o = overview.data;
    const checkedAt = o.instance_last_health_check_at;
    let tone = INSTANCE_TONE[o.instance_status] ?? "neutral";
    let detail: ReactNode;
    if (checkedAt === null) {
      tone = "neutral";
      detail = [o.instance_version, "never checked"].filter(Boolean).join(" · ");
    } else if (checkedAt === undefined) {
      detail = [o.instance_version, "check time not reported"].filter(Boolean).join(" · ");
    } else {
      detail = (
        <>
          {o.instance_version ? `${o.instance_version} · ` : null}
          checked <UtcTime value={checkedAt} variant="compact" />
          {ageText(checkedAt, now)}
        </>
      );
    }
    out.push({
      key: "instance",
      name: "QRadar instance",
      tone,
      value: titleCase(o.instance_status),
      detail,
    });
  }
  // overview.state === "forbidden": omitted.

  // 3. Collection: the last volume-search run.
  if (cadence.state === "loading") {
    out.push({ key: "collection", name: "Collection", tone: "neutral", value: "Checking", detail: null });
  } else if (cadence.state === "error") {
    out.push({
      key: "collection",
      name: "Collection",
      tone: "neutral",
      value: "Unavailable",
      detail: "The collection status could not be read.",
    });
  } else if (cadence.state === "ok") {
    const volume = cadence.data.find((c) => c.family === "PHASE_A_VOLUME");
    const run = volume?.last_run ?? null;
    if (!run) {
      out.push({
        key: "collection",
        name: "Collection",
        tone: "neutral",
        value: "No run recorded",
        detail: "Source volume has not run since the last upgrade.",
      });
    } else {
      const info = run.outcome ? outcomeInfo(run.outcome) : null;
      out.push({
        key: "collection",
        name: "Collection",
        tone: info?.tone ?? "neutral",
        value: info?.label ?? run.outcome ?? "Outcome not recorded",
        detail: (
          <>
            Source volume, last run <UtcTime value={run.at} variant="compact" />
            {ageText(run.at, now)}
          </>
        ),
      });
    }
  }
  // cadence.state === "forbidden": omitted.

  return out;
}

export function ConnectionStatus({
  health,
  overview,
  cadence,
}: {
  health: Loaded<HealthReady>;
  overview: Loaded<SocOverview>;
  cadence: Loaded<SearchCadenceItem[]>;
}) {
  // "Now" is the browser's clock, so ages render after mount only.
  const [now, setNow] = useState<number | null>(null);
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => setNow(Date.now()), [health, overview, cadence]);

  // Close on Escape and on a click elsewhere: a native <details> does neither.
  useEffect(() => {
    const close = () => {
      if (ref.current?.open) ref.current.open = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, []);

  const signals = buildSignals(health, overview, cadence, now);
  const worst = worstTone(signals) ?? "neutral";
  const word = signals.every((s) => s.value === "Checking") ? "Checking" : WORD[worst];
  const toneClass = worst === "neutral" ? "" : worst;

  return (
    <details className="topbar-status" ref={ref}>
      <summary className="topbar-status-summary" aria-label={`Connection status: ${word}`}>
        <span className="topbar-label" aria-hidden="true">
          Status
        </span>
        <span className={`pill ${toneClass}`.trim()} aria-hidden="true">
          {word}
        </span>
      </summary>
      <ul className="topbar-status-list" aria-label="Connection signals">
        {signals.map((s) => (
          <li key={s.key} className="topbar-status-row">
            <span className="topbar-status-name">{s.name}</span>
            <span className={`pill ${s.tone === "neutral" ? "" : s.tone}`.trim()}>{s.value}</span>
            {s.detail && <span className="topbar-status-detail">{s.detail}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
