"use client";

// The schedule of one stored search, with the seven presets as a picker.
//
// A custom cron is shown verbatim and is never rewritten unless the analyst
// picks a preset. Picking one is a PATCH of `schedule_cron` alone; the API's
// answer decides, and on a refusal the picker reverts.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { api } from "@/lib/api";
import {
  SCHEDULE_PRESETS,
  changeFailure,
  cronToPreset,
  everyLabel,
  presetToCron,
} from "@/lib/searchCadence";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "@/components/ui/controls.css";

/** The picker's value while the stored cron matches no preset. */
const KEEP_CUSTOM = "custom";

export function ScheduleControl({ searchId, cron }: { searchId: string; cron: string }) {
  const router = useRouter();
  const t = useTranslations("searches.schedule");
  const i18n = useBehaviorI18n();
  const selectId = useId();
  const [current, setCurrent] = useState(cron);
  const [pending, setPending] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });

  const preset = cronToPreset(current);
  const shown = pending ?? (preset ? String(preset.seconds) : KEEP_CUSTOM);

  async function change(value: string) {
    if (pending !== null || value === KEEP_CUSTOM) return;
    const next = presetToCron(Number(value));
    if (next === null || next === current) return;
    setPending(value);
    setOutcome({ kind: "pending" });
    try {
      const updated = await api.setSearchSchedule(searchId, next);
      setCurrent(updated.schedule_cron);
      setOutcome({ kind: "ok", text: t("saved") });
      // Bring the recomputed next-run time into the header.
      router.refresh();
    } catch (err) {
      setOutcome(changeFailure(err, t("forbidden"), i18n));
    } finally {
      setPending(null);
    }
  }

  return (
    <div>
      <div className="setting-line">
        <span>
          <strong><ScheduleLabel cron={current} /></strong>
        </span>
        <label htmlFor={selectId} className="muted">
          {t("changeTo")}
        </label>
        <select
          id={selectId}
          className="select-control"
          value={shown}
          disabled={pending !== null}
          aria-busy={pending !== null}
          onChange={(e) => void change(e.target.value)}
        >
          {!preset && (
            <option value={KEEP_CUSTOM}>{t("customKeep")}</option>
          )}
          {SCHEDULE_PRESETS.map((p) => (
            <option key={p.seconds} value={p.seconds}>
              {everyLabel(p.seconds, i18n)}
            </option>
          ))}
        </select>
      </div>
      <ChangeStatus outcome={outcome} />
    </div>
  );
}
