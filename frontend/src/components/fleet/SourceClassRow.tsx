"use client";

// The class of one source on its own page: what it is, why, and for a writer a select to change it.
//
// The row is read once by the server page and refreshed from the PATCH response, never from a
// guess. Permissions are not judged beyond hiding the control; the API refuses and the refusal
// is said in one line.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { useSession } from "@/components/auth/session";
import { ApiError, fleetApi, type FleetSourceRow, type SourceClass } from "@/lib/api";
import { ASSIGNABLE_CLASSES, basisLabel, classLabel, classTone } from "@/lib/fleet";
import { useFleetText } from "@/lib/fleetText";
import { WRITE_PERMISSION, holds } from "@/lib/fleetSources";

import "@/components/ui/controls.css";

const CLEAR = "CLEAR";
type Choice = SourceClass | typeof CLEAR | "";

/** Where a class that has a profile editor sends the analyst: the editor of this source. */
function profileLink(row: FleetSourceRow): { href: string; key: "create" | "open"; kind: "WAF" | "IPS" } | null {
  const kind = row.source_class === "WAF" ? "WAF" : row.source_class === "IPS" ? "IPS" : null;
  if (kind === null) return null;
  const profile = kind === "WAF" ? row.waf_profile : row.ips_profile;
  const base = kind === "WAF" ? "/admin/waf-sources" : "/admin/ips-sources";
  const href =
    profile === null
      ? `${base}/${row.id}?instance=${encodeURIComponent(row.instance_id)}`
      : `${base}/${row.id}`;
  return { href, key: profile === null ? "create" : "open", kind };
}

export function SourceClassRow({
  id,
  initial,
  failed,
}: {
  id: string;
  /** Null when the class could not be read (`failed`) or was not asked for. */
  initial: FleetSourceRow | null;
  failed: boolean;
}) {
  const t = useTranslations("fleet.sourceClass");
  const text = useFleetText();
  const { me, status } = useSession();
  const canWrite = holds(me, WRITE_PERMISSION);
  const [row, setRow] = useState<FleetSourceRow | null>(initial);
  const [choice, setChoice] = useState<Choice>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectId = useId();

  if (row === null) {
    return (
      <p className="muted">
        {t("label")} {failed ? t("readFailed") : "—"}
      </p>
    );
  }

  const overridden = row.override_class !== null;
  const link = profileLink(row);

  async function save() {
    if (choice === "" || saving) return;
    setSaving(true);
    setError(null);
    try {
      const next = await fleetApi.setOverride(id, choice === CLEAR ? null : choice);
      setChoice("");
      // Re-read the row so the profile link follows the new class; the 2xx already holds, so a
      // failed re-read keeps the saved class and the profile states read before.
      try {
        setRow(await fleetApi.sourceClass(id));
      } catch {
        setRow((prev) => (prev ? { ...prev, ...next } : prev));
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? t("forbidden", { permission: WRITE_PERMISSION })
          : err instanceof ApiError && err.status === 404
            ? t("notFound")
            : err instanceof ApiError && err.status === 422
              ? t("refused")
              : t("failed"),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="source-class-row">
      <p>
        {t("label")}{" "}
        <span className={`pill ${classTone(row.source_class)}`}>{classLabel(row.source_class, text)}</span>{" "}
        <span className="muted">
          {overridden
            ? row.override_by
              ? t("overrideBy", { name: row.override_by })
              : t("override")
            : basisLabel(row.basis, text)}
        </span>
        {link && (
          <>
            {" · "}
            <Link href={link.href}>{t(link.key, { kind: link.kind })}</Link>
          </>
        )}
      </p>
      {canWrite && status === "ready" && (
        <div className="row source-class-edit">
          <label htmlFor={selectId} className="sr-only">
            {t("newClass")}
          </label>
          <select
            id={selectId}
            value={choice}
            disabled={saving}
            onChange={(e) => {
              setChoice(e.target.value as Choice);
              setError(null);
            }}
          >
            <option value="">{t("choose")}</option>
            {ASSIGNABLE_CLASSES.filter((c) => c !== row.override_class).map((c) => (
              <option key={c} value={c}>
                {classLabel(c, text)}
              </option>
            ))}
            {overridden && <option value={CLEAR}>{t("clear")}</option>}
          </select>
          <button type="button" className="action" disabled={choice === "" || saving} onClick={() => void save()}>
            {saving ? t("saving") : t("save")}
          </button>
        </div>
      )}
      {error && (
        <p className="change-status crit" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
