"use client";

// The outage alert policy: the classes, the source overrides, and the editors.
//
// Holds the rows and the fleet summary, and reloads both after every change so
// the counts beside the table are the server's, not this component's guess.

import { useState } from "react";
import { useTranslations } from "next-intl";

import {
  outagePolicyApi,
  type FleetSource,
  type OutageFleetSummary,
  type OutageMode,
  type OutagePolicyRow,
} from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { ClassTable } from "./ClassTable";
import { PolicyForm } from "./PolicyForm";
import { SourcePicker } from "./SourcePicker";
import { SourceTable } from "./SourceTable";
import {
  classLines,
  classRow,
  defaultGraceText,
  defaultMode,
  modeLabel,
  sourceImpact,
  sourceRows,
} from "./outage";

import "@/components/ui/controls.css";

type Open = { key: string; kind: "edit" | "remove" } | null;
type Flash = { kind: "ok" | "crit"; text: string } | null;

export function OutagePolicyManager({
  initialRows,
  initialSummary,
  summaryNote,
}: {
  initialRows: OutagePolicyRow[];
  initialSummary: OutageFleetSummary | null;
  /** Why the fleet summary is missing, when it is. */
  summaryNote: string | null;
}) {
  const t = useTranslations("outage.manager");
  const i18n = useBehaviorI18n();
  const fleet = { say: i18n.t, locale: i18n.locale };
  const [rows, setRows] = useState(initialRows);
  const [summary, setSummary] = useState(initialSummary);
  const [flash, setFlash] = useState<Flash>(null);
  const [openClass, setOpenClass] = useState<Open>(null);
  const [openSource, setOpenSource] = useState<Open>(null);
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState<FleetSource | null>(null);

  const bucketSeconds = summary ? summary.bucket_seconds : null;
  const fallback = defaultMode(summary);
  const defaultGrace = defaultGraceText(summary, i18n);

  async function refresh(): Promise<boolean> {
    try {
      const [nextRows, nextSummary] = await Promise.all([
        outagePolicyApi.list(),
        summary ? outagePolicyApi.summary("24h").catch(() => null) : Promise.resolve(null),
      ]);
      setRows(nextRows);
      if (nextSummary) setSummary(nextSummary);
      return true;
    } catch {
      return false;
    }
  }

  async function changed(text: string) {
    setOpenClass(null);
    setOpenSource(null);
    const ok = await refresh();
    setFlash(
      ok
        ? { kind: "ok", text }
        : { kind: "crit", text: t("reloadFailed", { text }) },
    );
  }

  async function gone(text: string) {
    setOpenClass(null);
    setOpenSource(null);
    const ok = await refresh();
    setFlash({
      kind: "crit",
      text: ok ? text : t("reloadFailedGone", { text }),
    });
  }

  function classModeOf(cls: string | null): OutageMode | null {
    if (cls === null) return fallback;
    const row = classRow(rows, cls);
    if (row) return row.mode;
    return summary?.classes.find((c) => c.source_class === cls)?.outage_mode ?? fallback;
  }

  const lines = classLines(summary, rows, i18n);
  const overrides = sourceRows(rows);
  const overridden = new Set(
    overrides.filter((r) => r.instance_id === null).map((r) => r.scope_value),
  );

  return (
    <>
      {flash && (
        <div className={`banner outage-note ${flash.kind}`} role={flash.kind === "crit" ? "alert" : "status"}>
          <p>{flash.text}</p>
        </div>
      )}
      {summaryNote && (
        <div className="banner outage-note warn" role="status">
          <p>{summaryNote}</p>
        </div>
      )}
      {rows.length === 0 && (
        <div className="notice" role="status">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>{t("emptyText")}</p>
        </div>
      )}

      <section aria-labelledby="outage-classes">
        <h2 className="section-title" id="outage-classes">
          {t("classes")}
        </h2>
        <ClassTable
          lines={lines}
          rows={rows}
          summary={summary}
          bucketSeconds={bucketSeconds}
          open={openClass}
          setOpen={(v) => {
            setOpenClass(v);
            if (v) setFlash(null);
          }}
          onChanged={changed}
          onGone={gone}
        />
      </section>

      <section aria-labelledby="outage-sources">
        <h2 className="section-title" id="outage-sources">
          {t("sources")}
        </h2>
        {overrides.length === 0 ? (
          <p className="subtitle">{t("noOverrides")}</p>
        ) : (
          <SourceTable
            rows={overrides}
            classModeOf={classModeOf}
            bucketSeconds={bucketSeconds}
            defaultGrace={defaultGrace}
            open={openSource}
            setOpen={(v) => {
              setOpenSource(v);
              if (v) setFlash(null);
            }}
            onChanged={changed}
            onGone={gone}
          />
        )}

        <div className="outage-add stack-6">
          {!picking && !adding && (
            <button
              type="button"
              className="action"
              onClick={() => {
                setFlash(null);
                setPicking(true);
              }}
            >
              {t("addOverride")}
            </button>
          )}
          {picking && (
            <SourcePicker
              overridden={overridden}
              onEdit={(sourceId) => {
                setPicking(false);
                setFlash(null);
                setOpenSource({ key: `${sourceId}:`, kind: "edit" });
              }}
              onClose={() => setPicking(false)}
              onChoose={(s) => {
                setPicking(false);
                setAdding(s);
              }}
            />
          )}
          {adding && (
            <div className="panel">
              <PolicyForm
                label={t("overrideFor", { name: adding.name })}
                context={t("context", {
                  where: `${classLabel(adding.source_class, fleet)}${adding.type_name ? ` · ${adding.type_name}` : ""}`,
                  mode: classModeOf(adding.source_class)
                    ? modeLabel(classModeOf(adding.source_class) as OutageMode, i18n)
                    : t("modeUnreadable"),
                })}
                initialMode={classModeOf(adding.source_class) === "ALERT" ? "RECORD_ONLY" : "ALERT"}
                initialGrace={null}
                bucketSeconds={bucketSeconds}
                impactFor={(mode, grace) =>
                  sourceImpact(
                    {
                      name: adding.name,
                      source_class: adding.source_class,
                      classMode: classModeOf(adding.source_class),
                      fromMode: classModeOf(adding.source_class),
                      fromGrace: null,
                      toMode: mode,
                      toGrace: grace,
                      removal: false,
                      bucketSeconds,
                      defaultGrace,
                    },
                    i18n,
                  )
                }
                isUnchanged={(mode, grace) =>
                  grace === null && mode === classModeOf(adding.source_class)
                }
                onSave={async (body) => {
                  await outagePolicyApi.putSource(adding.id, body);
                  setAdding(null);
                  await changed(
                    t("savedSource", { name: adding.name, mode: modeLabel(body.mode, i18n) }),
                  );
                }}
                onCancel={() => setAdding(null)}
              />
            </div>
          )}
        </div>
      </section>
    </>
  );
}
