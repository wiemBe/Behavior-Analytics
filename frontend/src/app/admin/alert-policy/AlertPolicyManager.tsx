"use client";

// The alert policy: the rows, their editor, and the daily digest beneath them.
//
// Holds the rows and reloads them after every change so what is shown is the server's list, not
// this component's guess. Without `admin:outage` the table is read-only and says which permission
// edits it; while the session is still loading no claim is made either way.

import { useState } from "react";
import { useTranslations } from "next-intl";

import { useSession } from "@/components/auth/session";
import { alertPolicyApi, type AlertPolicyRow, type DigestStatus } from "@/lib/api";
import { holds, sortRows } from "@/lib/alertPolicy";

import { DigestConsoles, DigestPanel } from "./DigestPanel";
import { PolicyEditor } from "./PolicyEditor";
import { PolicyTable, type Open } from "./PolicyTable";

import "@/components/ui/controls.css";
import "./alertPolicy.css";

type Flash = { kind: "ok" | "crit"; text: string } | null;

export function AlertPolicyManager({
  initialRows,
  initialDigest,
  digestNote,
}: {
  initialRows: AlertPolicyRow[];
  initialDigest: DigestStatus | null;
  /** Why the digest status is missing, when it is. */
  digestNote: string | null;
}) {
  const t = useTranslations("alertPolicy");
  const session = useSession();
  const [rows, setRows] = useState(initialRows);
  const [digest, setDigest] = useState(initialDigest);
  const [flash, setFlash] = useState<Flash>(null);
  const [open, setOpen] = useState<Open>(null);
  const [adding, setAdding] = useState(false);

  const me = session.me;
  const permissions = me ? [...me.permissions, ...me.effective_permissions] : [];
  const known = session.status === "ready" || session.status === "error";
  const canWrite = known && holds(permissions, "admin:outage");

  async function refresh(): Promise<boolean> {
    try {
      const [next, status] = await Promise.all([
        alertPolicyApi.list(),
        digest ? alertPolicyApi.digest().catch(() => null) : Promise.resolve(null),
      ]);
      setRows(next);
      if (status) setDigest(status);
      return true;
    } catch {
      return false;
    }
  }

  async function changed(text: string, kind: "ok" | "crit" = "ok") {
    setOpen(null);
    setAdding(false);
    const ok = await refresh();
    setFlash(
      ok
        ? { kind, text }
        : { kind: "crit", text: t("flash.reloadFailed", { text }) },
    );
  }

  const sorted = sortRows(rows);

  return (
    <>
      <section aria-labelledby="policy-digest">
        <h2 className="section-title" id="policy-digest">
          {t("digest.heading")}
        </h2>
        <DigestPanel digest={digest} note={digest ? null : digestNote} />
      </section>

      <section className="policy-modes" aria-labelledby="policy-modes-title">
        <p className="policy-modes-title" id="policy-modes-title">
          {t("modes.heading")}
        </p>
        <dl>
          {(["ALERT", "RECORD_ONLY", "DIGEST"] as const).map((m) => (
            <div key={m}>
              <dt>{t(`mode.${m}`)}</dt>
              <dd>{t(`modes.${m}`)}</dd>
            </div>
          ))}
        </dl>
        <p>
          {t("modes.default")} {t("modes.precedence")}
        </p>
      </section>

      {flash && (
        <div
          className={`banner outage-note ${flash.kind}`}
          role={flash.kind === "crit" ? "alert" : "status"}
        >
          <p>{flash.text}</p>
        </div>
      )}

      <section aria-labelledby="policy-rows">
        <div className="policy-head">
          <h2 className="section-title" id="policy-rows">
            {t("table.heading")}
          </h2>
          {canWrite && !adding && (
            <button
              type="button"
              className="action"
              onClick={() => {
                setFlash(null);
                setOpen(null);
                setAdding(true);
              }}
            >
              {t("add")}
            </button>
          )}
        </div>
        {known && !canWrite && (
          <p className="subtitle" role="note">
            {t("readOnly")}
          </p>
        )}

        {adding && canWrite && (
          <div className="panel policy-add">
            <PolicyEditor
              rows={rows}
              editing={null}
              onSaved={(text) => changed(text)}
              onCancel={() => setAdding(false)}
            />
          </div>
        )}

        {sorted.length === 0 ? (
          <div className="notice" role="status">
            <p>
              <strong>{t("empty.title")}</strong>
            </p>
            <p>{t("empty.text")}</p>
          </div>
        ) : (
          <PolicyTable
            rows={sorted}
            allRows={rows}
            canWrite={canWrite}
            open={open}
            setOpen={(v) => {
              setOpen(v);
              if (v) setFlash(null);
            }}
            onChanged={(text) => changed(text)}
            onGone={(text) => changed(text, "crit")}
          />
        )}
      </section>

      <DigestConsoles digest={digest} />
    </>
  );
}
