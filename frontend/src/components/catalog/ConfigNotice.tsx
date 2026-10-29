"use client";

// The state of a configurable widget whose subject is not chosen yet, or no longer exists
// (UI-3.5b). It is a sentence, not an error and not a chart. While the dashboard is being
// customized it offers Configure; in read mode it says where to find it.

import { useTranslations } from "next-intl";
import { useContext } from "react";

import { WidgetRuntimeContext } from "@/widgets/runtime";

import "./catalog.css";

export function ConfigNotice({
  lead,
  text,
  hint,
}: {
  lead: string;
  text: string;
  /** Read mode only: where the analyst fixes it. */
  hint: string;
}) {
  const t = useTranslations("widgets.configNotice");
  const onConfigure = useContext(WidgetRuntimeContext)?.onConfigure ?? null;
  return (
    <div className="cw-config">
      <p className="tw-state" role="status">
        <strong>{lead}</strong> {text}
        {onConfigure ? "" : ` ${hint}`}
      </p>
      {onConfigure ? (
        <button
          type="button"
          className="action secondary"
          onClick={(event) => onConfigure(event.currentTarget)}
        >
          {t("configure")}
        </button>
      ) : null}
    </div>
  );
}
