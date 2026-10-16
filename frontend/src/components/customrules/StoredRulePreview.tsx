"use client";

import { useId, useState } from "react";

import { PreviewChart, PreviewSummary } from "@/components/customrules/PreviewChart";
import { api, type CustomRuleCatalog, type CustomRulePreview } from "@/lib/api";

export function StoredRulePreview({
  ruleId,
  subjectLabel,
  catalog,
  initial,
}: {
  ruleId: string;
  subjectLabel: string;
  catalog: CustomRuleCatalog;
  initial: CustomRulePreview;
}) {
  const hoursId = useId();
  const [hours, setHours] = useState(Math.min(24, catalog.preview_hours_max));
  const [preview, setPreview] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  async function load() {
    if (loading) return;
    setLoading(true);
    setFailed(false);
    try {
      setPreview(await api.customRulePreview(ruleId, hours));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="cr-stored-preview panel">
      <div className="cr-preview-head">
        <p>Stored measurements for this rule, with gaps left unconnected.</p>
        <div className="actions">
          <label htmlFor={hoursId}>
            Window
            <span className="cr-input-unit">
              <input
                id={hoursId}
                className="field num"
                type="number"
                min={catalog.preview_hours_min}
                max={catalog.preview_hours_max}
                value={hours}
                onChange={(event) => setHours(Number(event.target.value))}
              />
              <span>hours</span>
            </span>
          </label>
          <button type="button" className="action secondary" disabled={loading} onClick={() => void load()}>
            {loading ? "Loading…" : "Apply window"}
          </button>
        </div>
      </div>
      {failed ? (
        <div className="notice" role="alert">
          <p><strong>The stored-rule preview could not be loaded.</strong></p>
          <p>Check that the backend is reachable, then apply the window again.</p>
        </div>
      ) : (
        <div className="cr-preview-content">
          <PreviewChart preview={preview} subjectLabel={subjectLabel} />
          <PreviewSummary preview={preview} />
        </div>
      )}
    </div>
  );
}
