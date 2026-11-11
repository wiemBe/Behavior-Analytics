"use client";

// The handover page: the latest record, or the one named by `?id=`, then the earlier handovers.
// Compose is the same route with `?compose=1` (ComposeView).

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { asTextTranslator } from "@/i18n/textTranslator";
import { handoverApi, type HandoverRecord } from "@/lib/api";

import { useWriteAccess } from "./access";
import { EarlierList } from "./EarlierList";
import { handoverLoadReason } from "./loadError";
import { RecordView } from "./RecordView";

import "@/components/ui/controls.css";
import "./handover.css";

type Phase =
  | { status: "loading" }
  | { status: "error"; reason: string }
  | { status: "empty" }
  | { status: "ready"; record: HandoverRecord; latest: boolean };

export function HandoverView({ id }: { id: string | null }) {
  const t = useTranslations("handover");
  const say = asTextTranslator(useTranslations());
  const access = useWriteAccess();
  const [phase, setPhase] = useState<Phase>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setPhase({ status: "loading" });
    const read = id
      ? handoverApi.one(id).then((record): Phase => ({ status: "ready", record, latest: false }))
      : handoverApi
          .latest()
          .then((out): Phase =>
            out.handover ? { status: "ready", record: out.handover, latest: true } : { status: "empty" },
          );
    read
      .then((next) => {
        if (!cancelled) setPhase(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setPhase({ status: "error", reason: handoverLoadReason(err, say) });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, nonce]);

  if (phase.status === "loading") {
    return (
      <p className="notice" role="status" aria-live="polite">
        {t("loading")}
      </p>
    );
  }

  if (phase.status === "error") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("loadFailed.title")}</strong>
        </p>
        <p>{phase.reason}</p>
        <p>
          <button type="button" className="action secondary" onClick={() => setNonce((n) => n + 1)}>
            {t("retry")}
          </button>
          {id && (
            <>
              {" "}
              <Link href="/handover">{t("back")}</Link>
            </>
          )}
        </p>
      </div>
    );
  }

  if (phase.status === "empty") {
    return (
      <div className="notice ho-empty-state" role="status">
        <p>{t("empty.text")}</p>
        <p>
          <Link href="/queue">{t("empty.queue")}</Link>
          {access === "yes" && (
            <>
              {" · "}
              <Link href="/handover?compose=1">{t("empty.write")}</Link>
            </>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="ho">
      <RecordView record={phase.record} latest={phase.latest} />
      {!phase.latest && (
        <p>
          <Link href="/handover">{t("back")}</Link>
        </p>
      )}
      <EarlierList currentId={phase.record.id} />
    </div>
  );
}
