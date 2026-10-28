"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";


// Choosing which log source to configure.
//
// Lists the log sources that have no WAF profile, those QRadar classes as WAF
// first (the server orders them). Choosing one opens its editor in create mode;
// nothing is created here.

import { useEffect, useId, useState } from "react";

import { actionErrorMessage, api, type WafCandidateOut } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { formatCount } from "@/lib/format";

import "@/components/ui/controls.css";

type State =
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "ready"; items: WafCandidateOut[] };

const LIMIT = 50;

export function AddSourcePicker({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const t = useTranslations("waf.admin.picker");
  const i18n = useBehaviorI18n();
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
              text: t("failed", { message: actionErrorMessage(err, i18n.t) }),
            });
          }
        });
    }, q === "" ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, q, t, i18n.t]);

  return (
    <div className="fm-add">
      <button
        type="button"
        className="action"
        aria-expanded={open}
        aria-controls="fm-picker"
        onClick={() => setOpen((o) => !o)}
      >
        {t("add")}
      </button>
      {open && (
        <div id="fm-picker" className="fm-picker panel" role="region" aria-label={t("region")}>
          <div className="fm-picker-search">
            <label htmlFor={searchId}>{t("searchLabel")}</label>
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
              {t("searching")}
            </p>
          )}
          {state.kind === "error" && (
            <p className="fm-note is-crit" role="alert">
              {state.text}
            </p>
          )}
          {state.kind === "ready" && state.items.length === 0 && (
            <p className="fm-empty" role="status">
              {q.trim() ? t("emptyQuery", { q: q.trim() }) : t("empty")}
            </p>
          )}
          {state.kind === "ready" && state.items.length > 0 && (
            <>
              <ul className="fm-candidates" aria-label={t("listLabel")}>
                {state.items.map((c) => (
                  <li key={c.log_source_id}>
                    <Link href={`/admin/waf-sources/${c.log_source_id}?instance=${encodeURIComponent(c.instance_id)}`}>{c.name}</Link>
                    <span className="muted">
                      {t("qradarId", { id: formatCount(c.qradar_id, i18n.locale) })}
                      {c.type_name ? ` · ${c.type_name}` : ""}
                    </span>
                    {c.source_class === "WAF" && (
                      <span className="pill info">{t("classedAsWaf")}</span>
                    )}
                  </li>
                ))}
              </ul>
              {state.items.length >= LIMIT && (
                <p className="fm-note">
                  {t("capped", { limit: formatCount(LIMIT, i18n.locale) })}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
