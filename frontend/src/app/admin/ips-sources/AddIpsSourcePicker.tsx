"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";


// Choosing which log source to configure.
//
// Lists the log sources that have no IPS profile, those QRadar classes as IPS
// first (the server orders them). The search text matches name, type,
// identifier and QRadar id; each row shows the first three and the id. Choosing one opens its editor in create mode;
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
  const t = useTranslations("ips.picker");
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
                text: `${t("listFailed")} ${
                  err instanceof ApiError && err.status === 403
                    ? t("forbiddenList")
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
  }, [open, q, instanceId, t]);

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
          {!instanceId ? (
            <p className="fm-note is-crit" role="alert">
              {t("noInstance")}
            </p>
          ) : (
            <>
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
                  {q.trim() ? t("emptyQuery", { q: q.trim() }) : t("emptyAll")} {t("emptySync")}{" "}
                  <Link href="/log-sources">{t("emptyLink")}</Link>
                </p>
              )}
              {state.kind === "ready" && state.items.length > 0 && (
                <>
                  <div className="fm-candidates">
                    <table className="table-dense" aria-label={t("listLabel")}>
                      <thead>
                        <tr>
                          <th scope="col">{t("colSource")}</th>
                          <th scope="col">{t("colType")}</th>
                          <th scope="col">{t("colIdentifier")}</th>
                          <th scope="col" className="num">
                            {t("colQradarId")}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {state.items.map((c) => (
                          <tr key={c.log_source_id}>
                            <td className="cell-name">
                              <Link href={`/admin/ips-sources/${c.log_source_id}`}>{c.name}</Link>
                              {c.source_class === "IPS" && (
                                <>
                                  {" "}
                                  <span className="pill info">{t("classedIps")}</span>
                                </>
                              )}
                            </td>
                            <td className="cell-name">{c.type_name ?? ""}</td>
                            <td className="cell-name">
                              <span className="num">{c.identifier ?? ""}</span>
                            </td>
                            <td className="num">{c.qradar_id}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {state.items.length >= LIMIT && (
                    <p className="fm-note">{t("limited", { limit: LIMIT })}</p>
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
