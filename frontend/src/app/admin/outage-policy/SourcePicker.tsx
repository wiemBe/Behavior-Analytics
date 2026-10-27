"use client";

// Choosing the source to override.
//
// The search runs on the server (`q` on the fleet source list, a substring of
// the name or the QRadar type), debounced, so it covers the whole inventory. A
// page that holds fewer rows than matched says how many matched in total.

import { useEffect, useId, useState } from "react";
import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { actionErrorMessage, fleetApi, type FleetSource } from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

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
  const t = useTranslations("outage.picker");
  const i18n = useBehaviorI18n();
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
                text: t("listFailed", { reason: actionErrorMessage(err, i18n.t) }),
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
    // The translator is stable per locale; a changed locale re-reads nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  return (
    <div className="fm-picker panel" role="region" aria-label={t("region")}>
      <div className="fm-picker-search">
        <label htmlFor={searchId}>{t("search")}</label>
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
          {q.trim() ? t("emptyQuery", { query: q.trim() }) : t("empty")}
        </p>
      )}
      {state.kind === "ready" && state.items.length > 0 && (
        <TableScroll label={t("tableLabel")}>
          <table className="fleet-table outage-pick">
            <caption className="sr-only">{t("caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("source")}</th>
                <th scope="col">{t("class")}</th>
                <th scope="col">{t("type")}</th>
                <th scope="col" className="cell-end">
                  {t("override")}
                </th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((s) => (
                <tr key={s.id}>
                  <th scope="row" className="cell-source">
                    {s.name}
                    {!s.enabled && <span className="cell-sub">{t("disabled")}</span>}
                  </th>
                  <td>{classLabel(s.source_class, { say: i18n.t, locale: i18n.locale })}</td>
                  <td>{s.type_name ?? <span className="muted">—</span>}</td>
                  <td className="cell-end">
                    {overridden.has(s.id) ? (
                      <a
                        href={`#override-${s.id}`}
                        aria-label={t("hasOverrideAria", { name: s.name })}
                        onClick={(e) => {
                          e.preventDefault();
                          onEdit(s.id);
                        }}
                      >
                        {t("hasOverride")}
                      </a>
                    ) : (
                      <button
                        type="button"
                        className="action secondary"
                        aria-label={t("overrideAria", { name: s.name })}
                        onClick={() => onChoose(s)}
                      >
                        {t("override")}
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
          {t("capped", {
            total: plainCount(state.total, i18n.locale),
            shown: plainCount(state.items.length, i18n.locale),
          })}
        </p>
      )}
      <div className="actions">
        <button type="button" className="action secondary" onClick={onClose}>
          {t("close")}
        </button>
      </div>
    </div>
  );
}
