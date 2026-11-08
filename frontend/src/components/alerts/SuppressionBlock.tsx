"use client";

// The quiet-period block of an alert or anomaly detail page (SOC-17d): the banner when the
// condition is already quiet, and the "Don't alert this again" action. It owns the one read both
// parts depend on, so saving or ending a quiet period updates the banner without a page reload.

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { asTextTranslator } from "@/i18n/textTranslator";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { api, suppressionApi } from "@/lib/api";
import { detectorGroup, readFailure, type SuppressTarget } from "@/lib/suppression";

import { SuppressButton } from "./SuppressButton";
import { SuppressionBanner, type BannerState } from "./SuppressionBanner";
import "./suppression.css";

export function SuppressionBlock({ target }: { target: SuppressTarget }) {
  const say = asTextTranslator(useTranslations());
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const [state, setState] = useState<BannerState>({ kind: "loading" });
  const [message, setMessage] = useState<string | null>(null);
  // The source's name, from the anomaly the server reads the condition from. A failed read leaves
  // the wording generic; it never guesses a name.
  const [sourceName, setSourceName] = useState<string | null>(null);
  const { family, anomalyId } = target;

  const load = useCallback(async () => {
    try {
      setState({ kind: "ready", data: await suppressionApi.forAnomaly(family, anomalyId) });
    } catch (err) {
      setState(readFailure(err, say, ""));
    }
    // `say` is rebuilt every render and carries no state of its own.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [family, anomalyId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    api
      .anomaly(anomalyId)
      .then((a) => {
        if (!cancelled) setSourceName(a.log_source_name ?? null);
      })
      .catch(() => {
        if (!cancelled) setSourceName(null);
      });
    return () => {
      cancelled = true;
    };
  }, [anomalyId]);

  const data = state.kind === "ready" ? state.data : null;
  const active = data?.suppression ?? null;
  const detectorLabel = data ? enumLabel(enums, detectorGroup(family), data.detector_type) : null;
  // An alert whose title names another detector than its source anomaly's: say so before saving.
  const titled = target.via === "alert" ? target.alertDetector : null;
  const mismatchWith =
    data && titled && titled !== data.detector_type
      ? enumLabel(enums, detectorGroup(family), titled)
      : null;

  return (
    <div className="quiet-block">
      <SuppressionBanner
        state={state}
        onRetry={() => {
          setState({ kind: "loading" });
          void load();
        }}
        onEnded={async (text) => {
          setMessage(text);
          await load();
        }}
      />
      {message && (
        <p className="change-status ok" role="status">
          {message}
        </p>
      )}
      <SuppressButton
        target={target}
        detectorLabel={detectorLabel}
        sourceName={sourceName}
        mismatchWith={mismatchWith}
        active={active}
        onSaved={async () => {
          setMessage(null);
          await load();
        }}
      />
    </div>
  );
}
